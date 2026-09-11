"""What outcome a run records for each test.

Covers xfail, suites that mix marked and plain tests, and the dashboard hint
a failing run prints.
"""

import shlex
from pathlib import Path

import pytest

from evaltrack.cli import _build_parser  # pyright: ignore[reportPrivateUsage]

from .helpers import (
    DEFAULT_BODY,
    EVAL_TEST_SOURCE,
    find_nodeid,
    indent_block,
    make_eval_source,
    make_score_source,
    read_stored_attempts,
    run_eval_session,
    run_pytest,
)

pytest_plugins = ["pytester"]


# --- pytest outcome capture ---


_FAILING_TEST_SOURCE = make_eval_source(
    name="test_fails_after_recording",
    evaluator="return EvaluationReason(value=True)",
    body=DEFAULT_BODY
    + """\
# Independent assertion, unrelated to the eval. pytest sees this fail.
assert False, "deliberate fail"
""",
)


def test_pytest_outcome_passed_is_captured(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    result, run = run_eval_session(pytester, tmp_path, EVAL_TEST_SOURCE)
    result.assert_outcomes(passed=1)

    assert run.test("test_records_eval")["outcome"] == "passed"


def test_pytest_outcome_failed_is_captured(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A test that recorded a report and then failed a separate assertion saves
    the run with `outcome='failed'`. The eval passed and the test did not, and a
    reader must see that."""
    result, run = run_eval_session(pytester, tmp_path, _FAILING_TEST_SOURCE)
    result.assert_outcomes(failed=1)

    assert run.test("test_fails_after_recording")["outcome"] == "failed"


_PYTEST_FAIL_SOURCE = make_eval_source(
    name="test_fails_via_pytest_fail",
    body=DEFAULT_BODY + 'pytest.fail("deliberate fail")\n',
)


def test_pytest_fail_is_recorded_as_failed(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`pytest.fail()` raises `Failed`, not `AssertionError`, but it is a
    deliberate verdict failure. pytest reports FAILED, so the run must record
    `failed` and not `errored`, to agree with pytest."""
    result, run = run_eval_session(pytester, tmp_path, _PYTEST_FAIL_SOURCE)
    result.assert_outcomes(failed=1)

    assert run.test("test_fails_via_pytest_fail")["outcome"] == "failed"


_CRASHING_TEST_SOURCE = make_eval_source(
    name="test_crashes_after_recording",
    body=DEFAULT_BODY + 'raise RuntimeError("test body blew up")\n',
)


def test_non_assertion_exception_is_recorded_as_errored(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A non-assertion exception in the test body is a crash, not a verdict. It
    records as `errored`."""
    result, run = run_eval_session(pytester, tmp_path, _CRASHING_TEST_SOURCE)
    result.assert_outcomes(failed=1)

    assert run.test("test_crashes_after_recording")["outcome"] == "errored"


def _read_hint_command(out: str) -> str:
    """The command line the failure hint printed."""
    lines = [line.strip() for line in out.splitlines() if "evaltrack ui" in line]
    assert len(lines) == 1, f"expected exactly one hint line, got {lines!r}"
    return lines[0]


def _assert_ui_accepts(cmd: str) -> None:
    """Split the hint like a shell would and feed it to the real CLI parser.

    The hint exists to be copy-pasted, so an argument `evaltrack ui` rejects
    (parse_args exits with code 2) is a broken hint.
    """
    argv = shlex.split(cmd)
    assert argv[0] == "evaltrack"
    _build_parser().parse_args(argv[1:])


def test_failing_run_prints_dashboard_hint(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A failing run prints a hint that runs when pasted, also for a
    non-default repository. `evaltrack ui` resolves repositories from the
    same configuration the save target came from, so the hint stays bare."""
    repo_dir = tmp_path / "repo"
    pytester.makepyfile(test_x=_FAILING_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={repo_dir}")
    result.assert_outcomes(failed=1)
    cmd = _read_hint_command(result.stdout.str())
    assert cmd.startswith("evaltrack ui --run-id ")
    assert len(shlex.split(cmd)) == 4, "the hint carries nothing beyond the run id"
    _assert_ui_accepts(cmd)


def test_passing_run_omits_dashboard_hint(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={repo_dir}")
    result.assert_outcomes(passed=1)
    assert "evaltrack ui --run-id" not in result.stdout.str()


# --- mixed suites: only marked tests are recorded ---


_MIXED_SUITE_SOURCE = (
    make_eval_source(name="test_marked_eval")
    + """\

def test_plain_passing_unit():
    assert True

def test_plain_failing_unit():
    assert False, "an ordinary unit-test failure"
"""
)


def test_unmarked_tests_are_not_recorded(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """In a mixed suite, only `@pytest.mark.evaltrack`-marked tests reach the
    saved run. Plain unit tests, passing or failing, do not show up as empty
    entries."""
    result, run = run_eval_session(pytester, tmp_path, _MIXED_SUITE_SOURCE)
    result.assert_outcomes(passed=2, failed=1)

    assert list(run.data["tests"]) == [find_nodeid(run.data, "test_marked_eval")]


_MARKED_THEN_UNMARKED_EVALUATE_SOURCE = make_eval_source(
    name="test_marked_installs_patch",
    preamble="""\
class _Fail(Evaluator):
    def evaluate(self, ctx):
        return False
""",
    body="""\
dataset = Dataset(name='d', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
)


def test_failing_unmarked_test_does_not_trigger_dashboard_hint(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A failing plain unit test does not count as a run failure. With every
    marked eval green, the run has no failures and the dashboard hint stays
    quiet."""
    repo_dir = tmp_path / "repo"
    pytester.makepyfile(test_x=_MIXED_SUITE_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-repository={repo_dir}")
    result.assert_outcomes(passed=2, failed=1)
    assert "evaltrack ui --run-id" not in result.stdout.str()


_BROKEN_FIXTURE_SOURCE = (
    make_eval_source(
        name="test_passing_eval",
        preamble="""\
@pytest.fixture
def broken_fixture():
    raise RuntimeError("setup blew up")
""",
    )
    + """\

@pytest.mark.evaltrack
def test_with_broken_setup(broken_fixture):
    "Never runs."
"""
)


def test_a_setup_failure_is_recorded_as_errored_and_not_compounded(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A test whose fixture raises is recorded as `errored`, even though its call
    phase never runs, and the setup-phase outcome reaches the recorded test
    beside any test that did record reports. It never reached its body, so it
    never had the chance to evaluate either: the setup error stands alone,
    uncompounded by a complaint about the missing eval."""
    result, run = run_eval_session(pytester, tmp_path, _BROKEN_FIXTURE_SOURCE)
    result.assert_outcomes(passed=1, errors=1)
    assert "recorded no eval" not in result.stdout.str()

    assert run.test("test_passing_eval")["outcome"] == "passed", (
        "the setup failure must not touch the sibling test"
    )
    assert run.test("test_with_broken_setup")["outcome"] == "errored"


def test_a_setup_failure_still_records_the_test_file_and_docstring(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A test that dies in setup never reaches the fixture that binds it, so
    whatever describes it has to be read from the report hook instead."""
    _, run = run_eval_session(pytester, tmp_path, _BROKEN_FIXTURE_SOURCE)
    recorded = run.test("test_with_broken_setup")

    assert recorded["test_file"] == "test_x.py"
    assert recorded["docstring"] == "Never runs."


_FAILING_TEARDOWN_FIXTURE = """\
@pytest.fixture
def failing_teardown():
    yield
    raise RuntimeError("teardown blew up")
"""


_FAILING_TEARDOWN_SOURCE = make_eval_source(
    name="test_teardown_fails",
    preamble=_FAILING_TEARDOWN_FIXTURE,
    args="failing_teardown",
)


def test_a_teardown_failure_is_recorded_as_errored(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """pytest errors a test whose teardown raised, and exits non-zero for it. A
    run that recorded the eval as `passed` would say the opposite of the gate
    the session just failed."""
    result, run = run_eval_session(pytester, tmp_path, _FAILING_TEARDOWN_SOURCE)
    result.assert_outcomes(passed=1, errors=1)

    assert run.test("test_teardown_fails")["outcome"] == "errored"


_FAILING_TEARDOWN_AFTER_FAILURE_SOURCE = make_eval_source(
    name="test_fails_then_teardown_fails",
    preamble=_FAILING_TEARDOWN_FIXTURE,
    args="failing_teardown",
    body=DEFAULT_BODY
    + """\
# Independent assertion, unrelated to the eval. pytest sees this fail.
assert False, "deliberate fail"
""",
)


def test_a_teardown_failure_keeps_an_existing_failure(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The test reached a verdict of its own, and that verdict is what a reader
    needs. A teardown that raised afterwards adds nothing to it."""
    result, run = run_eval_session(
        pytester, tmp_path, _FAILING_TEARDOWN_AFTER_FAILURE_SOURCE
    )
    result.assert_outcomes(failed=1, errors=1)

    assert run.test("test_fails_then_teardown_fails")["outcome"] == "failed"


_CLEAN_TEARDOWN_SOURCE = (
    make_eval_source(
        name="test_clean_teardown_after_pass",
        preamble="""\
@pytest.fixture
def clean_teardown():
    yield
    pathlib.Path('TORN_DOWN').write_text('yes')
""",
        args="clean_teardown",
    )
    + """\

@pytest.mark.evaltrack
def test_clean_teardown_after_failure(clean_teardown):
"""
    + indent_block(DEFAULT_BODY, 4)
    + '\n    assert False, "deliberate fail"\n'
)


def test_a_passing_teardown_leaves_the_call_outcome_alone(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """Teardown runs for every test, so the phase must stay silent unless it
    fails. Otherwise its own clean outcome would overwrite the verdict the test
    body reached."""
    result, run = run_eval_session(pytester, tmp_path, _CLEAN_TEARDOWN_SOURCE)
    result.assert_outcomes(passed=1, failed=1)
    assert (pytester.path / "TORN_DOWN").exists(), "the teardown phase must have run"

    assert run.test("test_clean_teardown_after_pass")["outcome"] == "passed"
    assert run.test("test_clean_teardown_after_failure")["outcome"] == "failed"


_UNMARKED_FAILING_TEARDOWN_SOURCE = (
    make_eval_source(name="test_marked_eval", preamble=_FAILING_TEARDOWN_FIXTURE)
    + """\

def test_plain_unit(failing_teardown):
    assert True
"""
)


def test_a_teardown_failure_on_an_unmarked_test_is_not_recorded(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """Only marked tests reach the saved run, whichever phase failed."""
    result, run = run_eval_session(
        pytester, tmp_path, _UNMARKED_FAILING_TEARDOWN_SOURCE
    )
    result.assert_outcomes(passed=2, errors=1)

    assert list(run.data["tests"]) == [find_nodeid(run.data, "test_marked_eval")]


_NO_EVAL_FAILING_TEARDOWN_SOURCE = (
    make_eval_source(
        name="test_never_evaluates",
        preamble=_FAILING_TEARDOWN_FIXTURE,
        args="failing_teardown",
        body="assert True\n",
    )
    + "\n@pytest.mark.evaltrack\ndef test_evaluates():\n"
    + indent_block(DEFAULT_BODY, 4)
    + "\n"
)


def test_a_teardown_failure_after_a_missing_eval_stays_errored(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A marked test that evaluated nothing is already errored, for a reason the
    failure text spells out. A teardown that raised afterwards must not restate
    that verdict as its own."""
    result, run = run_eval_session(pytester, tmp_path, _NO_EVAL_FAILING_TEARDOWN_SOURCE)
    result.assert_outcomes(passed=1, failed=1, errors=1)
    assert "recorded no eval" in result.stdout.str()

    assert run.test("test_never_evaluates")["outcome"] == "errored"


_RAISING_TASK_SOURCE = make_eval_source(
    name="test_with_raising_task",
    evaluator="return EvaluationReason(value=True)",
    task='raise RuntimeError("task blew up")',
    body="""\
dataset = Dataset(
    name="d",
    cases=[Case(name="c", inputs="x", expected_output="x")],
    evaluators=[_Evaluator()],
)
# `evaltrack.run` must turn task failures into an EvalExecutionError itself. The
# test body checks nothing.
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
)


def test_a_crashed_task_errors_the_test_and_is_recorded_as_an_errored_attempt(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A task that raises inside Dataset.evaluate normally produces a report
    with `failures` populated and returns normally. With the evaltrack patch,
    that surfaces as an EvalExecutionError and pytest errors the test, which is what
    users almost always want. The crashed case must reach the saved run too: a
    reader works from the recorded cases, and a crash missing from them leaves
    no trace."""
    result, run = run_eval_session(pytester, tmp_path, _RAISING_TASK_SOURCE)
    result.assert_outcomes(failed=1)
    combined = result.stdout.str() + result.stderr.str()
    assert "task failures" in combined.lower(), (
        "the error must mention the report's failure"
    )

    case = run.case("test_with_raising_task")
    assert case["clean_attempts"] == 0, (
        "the only attempt errored, so none reached a verdict"
    )
    assert case["errored_attempts"] == 1
    assert case["passed_attempts"] == 0
    assert case["outcome"] == "errored"
    (attempt,) = read_stored_attempts(case)
    assert attempt["outcome"] == "errored"


def test_crashed_per_case_evaluator_fails_the_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A per-case evaluator that crashed leaves no assertion behind, so a judge
    that never ran would otherwise pass the eval. It errors the test, and the
    recorded case agrees rather than reading as passed."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_judge_down",
            evaluator="raise RuntimeError('judge API down')",
            body="""\
dataset = Dataset(name='d', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
    )
    result.assert_outcomes(failed=1)
    assert "per-case evaluator failures" in result.stdout.str()

    test = run.test("test_judge_down")
    case = test["cases"]["c"]
    assert case["outcome"] != "passed", "a judge that never ran must not read as a pass"
    assert read_stored_attempts(case)[0]["outcome"] == "errored"


def test_crashed_report_level_evaluator_fails_the_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A report-level evaluator crash leaves every case passing, so nothing in
    the per-case data shows it. Without the raise the test would go green and
    the crash would leave no trace."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_report_evaluator_down",
            evaluator="return EvaluationReason(value=True)",
            preamble="""\
from pydantic_evals.evaluators import ReportEvaluator

class _ReportEvaluator(ReportEvaluator):
    def evaluate(self, ctx):
        raise RuntimeError('summary stats blew up')
""",
            body="""\
dataset = Dataset(
    name='d',
    cases=[Case(name='c', inputs='x')],
    evaluators=[_Evaluator()],
    report_evaluators=[_ReportEvaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
    )
    result.assert_outcomes(failed=1)
    assert "round-level failures" in result.stdout.str()

    test = run.test("test_report_evaluator_down")
    assert test["outcome"] == "errored"


def test_fails_when_every_task_crashed(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An eval where every task crashed fails the test. pydantic-evals moves a
    crashed case out of `report.cases`, so such a report records no cases at
    all. It must not read as a run that evaluated nothing and passed."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_everything_crashed",
            evaluator="return EvaluationReason(value=True)",
            task="raise RuntimeError('rate limited')",
            body="""\
dataset = Dataset(
    name='d',
    cases=[Case(name='a', inputs='x'), Case(name='b', inputs='y')],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*task failures: a *, b *"])

    test = run.test("test_everything_crashed")
    assert test["outcome"] == "errored", (
        "a report with no cases must not read as passed"
    )


def test_gate_reports_a_missing_score_bar(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A score bar that names a score nothing produced stays a `ValueError` when
    the cases ran. Only a crashed task suppresses that message, because a task
    that never returned could not produce the score either."""
    result, _ = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_typo_bar",
            marker="@pytest.mark.evaltrack(score_bars={'nope': 0.5})",
            evaluator="return EvaluationReason(value=True)",
            body="""\
dataset = Dataset(
    name='d', cases=[Case(name='a', inputs='x')], evaluators=[_Evaluator()]
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
    )
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*name a score no case produced*"])


# --- xfail: acknowledged failures that do not fail CI ---


def test_xfail_failing_eval_recorded_as_xfailed(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A failing eval marked `xfail` does not fail CI. It records as `xfailed`,
    an acknowledged failure, rather than `skipped`, and the failing per-case
    verdict is still captured for the dashboard."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_score_source(
            0.5,
            marker=(
                "@pytest.mark.evaltrack(score_bars={'quality': 0.8})\n"
                "@pytest.mark.xfail(reason='known broken')"
            ),
        ),
    )
    result.assert_outcomes(xfailed=1)

    test = run.test("test_scored")
    assert test["outcome"] == "xfailed"
    assert test["cases"]["c"]["outcome"] != "passed", (
        "xfail must not mask the failing verdict"
    )


def test_xfail_absorbs_an_eval_refused_by_the_gate(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An eval the gate refuses raises `EvalDefinitionError`, not an
    `AssertionError`. `xfail` is how a marked test records without failing CI,
    so it must absorb that refusal too, not only a failing verdict."""
    result, _ = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_one_case_is_unevaluated",
            marker=(
                "@pytest.mark.evaltrack\n"
                "@pytest.mark.xfail(reason='a case nothing judges', strict=True)"
            ),
            evaluator="return ctx.output == ctx.expected_output",
            body="""\
dataset = Dataset(
    name='d',
    cases=[
        Case(name='judged', inputs='x', expected_output='x', evaluators=[_Evaluator()]),
        Case(name='unjudged', inputs='y', expected_output='y'),
    ],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
    )
    result.assert_outcomes(xfailed=1)


def test_xfail_passing_eval_recorded_as_xpassed(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An eval that unexpectedly passes under a (non-strict) `xfail` is recorded
    as `xpassed`, so a since-fixed eval is visible as such."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_score_source(
            0.85,
            marker=(
                "@pytest.mark.evaltrack(score_bars={'quality': 0.8})\n"
                "@pytest.mark.xfail(reason='thought it was broken')"
            ),
        ),
    )
    result.assert_outcomes(xpassed=1)

    assert run.test("test_scored")["outcome"] == "xpassed"


# --- a marked test that never evaluates ---


_NO_EVAL_SOURCE = make_eval_source(
    name="test_never_evaluates",
    marker="@pytest.mark.evaltrack(score_bars={'quality': 0.9})",
    body="assert True\n",
)


def test_marked_test_that_never_evaluates_fails(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The marker declares the test is an eval, so a pass without one means
    nothing checked the declared score bars, and nothing was recorded. That is
    the one failure evaltrack cannot report from the eval itself."""
    result, _ = run_eval_session(pytester, tmp_path, _NO_EVAL_SOURCE)
    result.assert_outcomes(failed=1)

    output = result.stdout.str()
    assert "test_x.py::test_never_evaluates" in output, "the failure must name the test"
    assert "recorded no eval" in output
    # Both ways out, and why an eval that did run can still not count.
    assert "evaltrack.run()" in output
    assert "drop the marker" in output
    assert "fixture" in output


_MODULE_FIXTURE_EVAL_SOURCE = make_eval_source(
    name="test_evaluates_in_a_module_fixture",
    preamble="""\
@pytest.fixture(scope="module")
def report():
    dataset = Dataset(
        name="d", cases=[Case(name="c", inputs="x")], evaluators=[_Evaluator()]
    )
    return asyncio.run(dataset.evaluate(_task))
""",
    args="report",
    body="assert report is not None\n",
)


def test_eval_from_a_wider_fixture_fails_the_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An eval from a fixture wider than the test runs outside the tracking
    window, so it is neither recorded nor gated. The test must not pass on it."""
    result, _ = run_eval_session(pytester, tmp_path, _MODULE_FIXTURE_EVAL_SOURCE)
    result.assert_outcomes(failed=1)
    assert "recorded no eval" in result.stdout.str()


_SKIPPED_WITHOUT_EVAL_SOURCE = (
    make_eval_source(
        name="test_skips_in_the_body",
        preamble="""\
@pytest.fixture
def skipping_fixture():
    pytest.skip("not today")
""",
        body='pytest.skip("not today")\n',
    )
    + """\

@pytest.mark.evaltrack
def test_skips_in_a_fixture(skipping_fixture):
    pass
"""
)


def test_skipped_marked_tests_stay_skipped(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A skipped test never had the chance to evaluate, whether it skipped in
    its body or in a fixture. Neither becomes a failure."""
    result, _ = run_eval_session(pytester, tmp_path, _SKIPPED_WITHOUT_EVAL_SOURCE)
    result.assert_outcomes(skipped=2)
    assert "recorded no eval" not in result.stdout.str()


@pytest.mark.parametrize(
    ("strict", "outcomes"),
    [(False, {"xpassed": 1}), (True, {"failed": 1})],
    ids=["non-strict", "strict"],
)
def test_xfail_without_an_eval_keeps_pytests_verdict(
    pytester: pytest.Pytester, tmp_path: Path, strict: bool, outcomes: dict[str, int]
) -> None:
    """`xfail` is how a marked test records without failing CI, so an
    xfail-flagged verdict is left as pytest decided it. A test that stops
    evaluating still surfaces: the unexpected pass is XPASS, and FAILED under
    `xfail_strict`."""
    result, _ = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_never_evaluates",
            marker=(
                "@pytest.mark.evaltrack\n"
                f"@pytest.mark.xfail(reason='known broken', strict={strict})"
            ),
            body="assert True\n",
        ),
    )
    result.assert_outcomes(**outcomes)
    assert "recorded no eval" not in result.stdout.str()


_FAILS_WITHOUT_EVAL_SOURCE = make_eval_source(
    name="test_fails_before_evaluating",
    body='assert False, "deliberate fail"\n',
)


def test_a_failing_test_keeps_its_own_failure(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A marked test that fails before it evaluates already reports loudly, and
    its own traceback is the one that explains why."""
    result, _ = run_eval_session(pytester, tmp_path, _FAILS_WITHOUT_EVAL_SOURCE)
    result.assert_outcomes(failed=1)
    assert "deliberate fail" in result.stdout.str()
    assert "recorded no eval" not in result.stdout.str()


_MIXED_EVAL_SOURCE = (
    _NO_EVAL_SOURCE
    + "\n@pytest.mark.evaltrack\ndef test_evaluates():\n"
    + indent_block(DEFAULT_BODY, 4)
    + "\n"
)


def test_a_test_without_an_eval_is_recorded_as_errored(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The run must show what happened. A test that never evaluated reached no
    verdict of its own, so it records `errored` with no eval beside it. `failed`
    would read as an eval whose cases fell short."""
    result, run = run_eval_session(pytester, tmp_path, _MIXED_EVAL_SOURCE)
    result.assert_outcomes(passed=1, failed=1)

    test = run.test("test_never_evaluates")
    assert test["outcome"] == "errored"
    assert test["marker"] is None, "a test that never evaluated has no eval config"
    assert run.test("test_evaluates")["outcome"] == "passed"
