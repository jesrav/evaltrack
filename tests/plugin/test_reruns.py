"""Repeated rounds of one eval.

Covers the marker's `repeats=` (satisfied natively by `evaluate(repeat=N)` or
by driven rounds), the `flake_reruns=` retry loop, and whole-test reruns driven
by pytest-rerunfailures.
"""

from pathlib import Path

import pytest

from .helpers import (
    FLAKY_ONCE_EVALUATOR,
    FLAKY_ONCE_PREAMBLE,
    make_eval_source,
    read_stored_attempts,
    run_eval_session,
)

pytest_plugins = ["pytester"]


# --- repeats= (native fulfillment and driven rounds) ---


def _repeat_source(marker: str) -> str:
    return make_eval_source(
        name="test_repeated",
        marker=marker,
        body="""\
dataset = Dataset(
    name='d', cases=[Case(name='c', inputs='x', expected_output='x')], evaluators=[_Evaluator()]
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task, repeat=3)))
""",
    )


def test_native_repeat_with_flake_reruns_is_refused(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A native repeat demands that every attempt pass, and a flake rerun lets
    one pass settle the case, so the two together have no coherent gate."""
    result, _ = run_eval_session(
        pytester, tmp_path, _repeat_source("@pytest.mark.evaltrack(flake_reruns=1)")
    )
    result.assert_outcomes(failed=1)
    assert "opposite directions" in result.stdout.str()


def test_native_repeat_count_must_match_the_marker(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    result, _ = run_eval_session(
        pytester, tmp_path, _repeat_source("@pytest.mark.evaltrack(repeats=5)")
    )
    result.assert_outcomes(failed=1)
    assert "repeated each case 3 times" in result.stdout.str()
    assert "repeats=5" in result.stdout.str()


_ACCESSOR_WIRED_SOURCE = make_eval_source(
    name="test_accessor_wired",
    marker="@pytest.mark.evaltrack(repeats=2)",
    preamble="_evaluator_calls = {'n': 0}",
    evaluator="""\
_evaluator_calls["n"] += 1
return True
""",
    body="""\
dataset = Dataset(
    name='d', cases=[Case(name='c', inputs='x', expected_output='x')], evaluators=[_Evaluator()]
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task, repeat=evaltrack.repeats())))
assert _evaluator_calls["n"] == 2, "one round produced both demanded attempts"
""",
)


def test_the_accessor_wires_the_native_repeat_from_the_marker(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`evaltrack.repeats()` hands the marker's count to the runner, so the
    count is written once and the native path cannot drift from the gate."""
    result, run = run_eval_session(pytester, tmp_path, _ACCESSOR_WIRED_SOURCE)
    result.assert_outcomes(passed=1)

    case = run.case("test_accessor_wired")
    assert case["clean_attempts"] == 2
    assert case["outcome"] == "passed"


_DRIVEN_REPEATS_SOURCE = make_eval_source(
    name="test_driven",
    marker="@pytest.mark.evaltrack(repeats=3)",
    preamble="_calls = {'n': 0}",
    evaluator="""\
_calls["n"] += 1
return _calls["n"] < 3   # the third round fails
""",
)


def test_driven_repeats_run_every_round_and_demand_all_pass(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An eval that does not repeat natively is run once per demanded attempt,
    unconditionally, and a pass in an early round settles nothing."""
    result, run = run_eval_session(pytester, tmp_path, _DRIVEN_REPEATS_SOURCE)
    result.assert_outcomes(failed=1)

    test = run.test("test_driven")
    case = test["cases"]["c"]
    assert case["clean_attempts"] == 3, "all three rounds ran despite the passes"
    assert case["passed_attempts"] == 2
    assert case["outcome"] == "failed", "two passes do not satisfy repeats=3"


# --- reruns (test-level rerun-on-failure) ---


# Module-global call counter. The evaluator fails the first call and passes the
# second, so the case fails first and passes when re-run.
_RERUN_SOURCE = make_eval_source(
    name="test_flaky",
    marker="@pytest.mark.evaltrack(flake_reruns=2)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator=FLAKY_ONCE_EVALUATOR,
)


def test_reruns_recover_a_flaky_test(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """A test that fails once and then passes is rerun and ends as passed. Both
    attempts are recorded, and the failing first one is kept."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_SOURCE)
    result.assert_outcomes(passed=1)

    test = run.test("test_flaky")
    assert test["outcome"] == "passed"
    case = test["cases"]["c"]
    assert case["clean_attempts"] == 2
    assert case["passed_attempts"] == 1
    assert case["outcome"] == "passed"
    assert case["attempts"][0]["outcome"] == "failed", (
        "the failing first attempt must be kept"
    )


_RERUN_ALWAYS_FAILS_SOURCE = make_eval_source(
    name="test_always_fails",
    marker="@pytest.mark.evaltrack(flake_reruns=2)",
    evaluator="return False",
)


def test_reruns_exhausted_fails_once(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """A persistently failing case exhausts its reruns and the test reports a
    single failure. Every attempt is recorded."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_ALWAYS_FAILS_SOURCE)
    result.assert_outcomes(failed=1)

    test = run.test("test_always_fails")
    case = test["cases"]["c"]
    assert case["clean_attempts"] == 3, "the first attempt plus both reruns"
    assert case["passed_attempts"] == 0
    assert case["outcome"] != "passed"


# Errors on the first attempt, and would pass from the second. A rerun retries
# a verdict failure, but never an error, so this never reaches the passing
# second attempt.
_RERUN_ERRORS_SOURCE = make_eval_source(
    name="test_errors",
    marker="@pytest.mark.evaltrack(flake_reruns=2)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator="""\
_calls["n"] += 1
if _calls["n"] == 1:
    raise RuntimeError("transient blow-up")
return True
""",
)


def test_reruns_do_not_rescue_an_error(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An exception errors the test at once. Reruns absorb flakiness in the
    output, not errors, so the second attempt that would pass is never run."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_ERRORS_SOURCE)
    result.assert_outcomes(failed=1)  # not rescued into a pass

    test = run.test("test_errors")
    assert test["outcome"] == "errored"
    assert len(test["cases"]["c"]["attempts"]) == 1, "it ran once and did not rerun"


_RERUN_PER_CASE_SOURCE = make_eval_source(
    name="test_per_case",
    marker="@pytest.mark.evaltrack(flake_reruns=2)",
    # Count evaluator calls per case input to show what a round actually costs.
    preamble="_calls = {}",
    evaluator="""\
x = ctx.inputs
_calls[x] = _calls.get(x, 0) + 1
if x == 'b':
    return _calls[x] >= 2   # 'b' fails first, passes on rerun
return True                 # 'a' and 'c' always pass
""",
    body="""\
dataset = Dataset(
    name='d',
    cases=[Case(name='a', inputs='a'), Case(name='b', inputs='b'), Case(name='c', inputs='c')],
    evaluators=[_Evaluator()],
)
# A round re-runs the whole eval, so every case is evaluated twice, not just 'b'.
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
assert _calls == {'a': 2, 'b': 2, 'c': 2}, _calls
""",
)


def test_a_round_re_runs_every_case(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """A round re-runs the whole eval, so every case gains an attempt, not only
    the flaky one. That costs more model calls than a retry of the failure
    alone. It also buys a second sample for the cases that would otherwise never
    get one, and the pass-rate history pools those samples."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_PER_CASE_SOURCE)
    result.assert_outcomes(passed=1)

    cases = run.test("test_per_case")["cases"]
    assert cases["b"]["clean_attempts"] == 2, "failed once, reran, passed"
    assert cases["b"]["attempts"][0]["outcome"] == "failed"
    assert cases["b"]["outcome"] == "passed"
    assert cases["a"]["clean_attempts"] == 2, "a settled case runs again too"
    assert cases["c"]["clean_attempts"] == 2
    assert cases["a"]["outcome"] == "passed", (
        "and a later round cannot take away the pass it already had"
    )


# The task passes on the initial run but crashes when the failing case is
# re-run, so the rerun's sub-report carries the case under `failures`, not
# `cases`.
_RERUN_TASK_CRASHES_SOURCE = make_eval_source(
    name="test_rerun_task_crashes",
    marker="@pytest.mark.evaltrack(flake_reruns=1)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator="return False",
    task="""\
_calls["n"] += 1
if _calls["n"] > 1:
    raise RuntimeError("rerun task blew up")
return x
""",
)


def test_crashed_rerun_does_not_erase_the_failing_case(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A failing case whose rerun task crashes must not vanish from the recorded
    run. The crash errors the test, and the run keeps the case with its failing
    first verdict and both attempts, the crashed one included."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_TASK_CRASHES_SOURCE)
    result.assert_outcomes(failed=1)

    test = run.test("test_rerun_task_crashes")
    assert test["outcome"] == "errored"
    case = test["cases"]["c"]
    assert case["outcome"] == "errored", "the crash outranks the failing attempt"
    assert case["clean_attempts"] == 1, (
        "only the failing first attempt reached a verdict"
    )
    assert case["errored_attempts"] == 1, "the crashed rerun counts as an error"
    assert [a["outcome"] for a in read_stored_attempts(case)] == ["failed", "errored"]


_RERUN_MIXED_NAMES_SOURCE = make_eval_source(
    name="test_mixed_names",
    marker="@pytest.mark.evaltrack(flake_reruns=1)",
    preamble="_calls = {}",
    evaluator="""\
x = ctx.inputs
_calls[x] = _calls.get(x, 0) + 1
return _calls[x] >= 2   # both cases fail first, pass on rerun
""",
    body="""\
dataset = Dataset(
    name='d',
    cases=[Case(name='named', inputs='named'), Case(inputs='unnamed')],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))  # Both failing cases are re-run, the unnamed one included.
assert _calls == {'named': 2, 'unnamed': 2}, _calls
""",
)


def test_reruns_a_named_and_an_unnamed_case_together(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A named case next to an unnamed one used to leave the unnamed one behind,
    which spent the rerun budget on half the failures. Both are retried, and the
    unnamed one is keyed by the runner's name for its row in both rounds."""
    result, run = run_eval_session(pytester, tmp_path, _RERUN_MIXED_NAMES_SOURCE)
    result.assert_outcomes(passed=1)

    cases = run.test("test_mixed_names")["cases"]
    assert [case["clean_attempts"] for case in cases.values()] == [2, 2]
    assert sorted(cases) == ["Case 2", "named"]


_UNNAMED_CASES_SOURCE = make_eval_source(
    name="test_unnamed_cases",
    marker="@pytest.mark.evaltrack(flake_reruns=1)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator=FLAKY_ONCE_EVALUATOR,
    body="""\
def evaluate():
    dataset = Dataset(
        name='d', cases=[Case(inputs='x'), Case(inputs='y')], evaluators=[_Evaluator()]
    )
    return asyncio.run(dataset.evaluate(_task))

evaltrack.run(evaluate)
""",
)


def test_reruns_fold_unnamed_cases_by_row(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """pydantic-evals names an unnamed case after its row, so a round is folded
    into the last on that row as it would be on a chosen name. The eval must
    then build its dataset in the same order every round, which is on the user."""
    result, run = run_eval_session(pytester, tmp_path, _UNNAMED_CASES_SOURCE)
    result.assert_outcomes(passed=1)
    cases = run.test("test_unnamed_cases")["cases"]
    assert sorted(cases) == ["Case 1", "Case 2"]
    assert cases["Case 1"]["clean_attempts"] == 2, "the flaky first row was rerun"


# --- whole-test reruns (pytest-rerunfailures) ---


# Always fails, so the flake rerun budget is spent and the test fails, which is
# what makes the rerun plugin re-invoke it.
_RERUN_AFTER_FLAKE_RERUNS_SOURCE = make_eval_source(
    name="test_evaluates_then_is_reinvoked",
    marker="@pytest.mark.evaltrack(flake_reruns=1)",
    evaluator="return False",
)


def test_a_whole_test_rerun_of_an_evaluated_test_is_refused(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A rerun plugin re-invokes the failed test, and its second `evaltrack.run`
    is refused. Two evals under one test have no single verdict, and the marker
    is where a test asks for repetition. The first eval is still recorded, so
    the rounds that were paid for survive the refusal."""
    result, run = run_eval_session(
        pytester, tmp_path, _RERUN_AFTER_FLAKE_RERUNS_SOURCE, "--reruns=1"
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("failed") == 1
    assert outcomes.get("rerun") == 1
    assert "already recorded an eval" in result.stdout.str()
    assert "flakiness.md#rerun-on-failure-flake_reruns" in result.stdout.str(), (
        "the refusal must point at how to keep the plugin off an eval test"
    )

    test = run.test("test_evaluates_then_is_reinvoked")
    assert test["outcome"] == "errored", "the refusal is a definition error"
    assert len(read_stored_attempts(test["cases"]["c"])) == 2, (
        "the first invocation's round and its flake rerun, and nothing from the second"
    )


# repeats=2 with an evaluator that fails only the very first attempt of the
# session: the first invocation goes 1 of 2 and fails, so the plugin re-invokes
# it, and the second invocation would otherwise go 2 of 2 and pass.
_RERUN_AFTER_REPEATS_SOURCE = make_eval_source(
    name="test_repeats_then_is_reinvoked",
    marker="@pytest.mark.evaltrack(repeats=2)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator=FLAKY_ONCE_EVALUATOR,
)


def test_a_whole_test_rerun_cannot_rescue_a_repeated_eval(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`repeats=` is refused the same way, and this is why it must be. The
    second invocation satisfies the count on its own, so a merge would store
    four attempts against a demand for two, and pytest would report the test as
    passed while the recorded case failed."""
    result, run = run_eval_session(
        pytester, tmp_path, _RERUN_AFTER_REPEATS_SOURCE, "--reruns=1"
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("failed") == 1, "the rerun cannot turn the session green"
    assert outcomes.get("rerun") == 1
    assert "already recorded an eval" in result.stdout.str()

    test = run.test("test_repeats_then_is_reinvoked")
    case = test["cases"]["c"]
    assert [a["outcome"] for a in read_stored_attempts(case)] == ["failed", "passed"]
    assert case["outcome"] == "failed", "1 of 2 does not satisfy repeats=2"


# The first invocation dies in a fixture, so it never reaches `evaltrack.run`.
_RERUN_OF_A_BROKEN_FIXTURE_SOURCE = make_eval_source(
    name="test_flaky_fixture",
    args="flaky_setup",
    preamble="""\
_setups = {"n": 0}

@pytest.fixture
def flaky_setup():
    _setups["n"] += 1
    if _setups["n"] == 1:
        raise RuntimeError("setup blew up")
    return "ready"
""",
)


def test_a_whole_test_rerun_after_a_broken_fixture_is_allowed(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The carve-out, and what a rerun plugin is worth on an eval test. An
    invocation that never evaluated leaves nothing to contradict, so the rerun
    evaluates like a first invocation, and the test passes on it."""
    result, run = run_eval_session(
        pytester, tmp_path, _RERUN_OF_A_BROKEN_FIXTURE_SOURCE, "--reruns=1"
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed") == 1
    assert outcomes.get("rerun") == 1
    assert "already recorded an eval" not in result.stdout.str()

    test = run.test("test_flaky_fixture")
    assert test["outcome"] == "passed"
    case = test["cases"]["c"]
    assert case["outcome"] == "passed"
    assert len(read_stored_attempts(case)) == 1, "only the invocation that evaluated"


def test_whole_test_rerun_of_a_crash_before_evaluate(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The same carve-out for a body that crashed on its way to
    `Dataset.evaluate`. The rerun records its eval, and the invocation that
    died leaves no empty round behind."""
    result, run = run_eval_session(
        pytester,
        tmp_path,
        make_eval_source(
            name="test_crashes_then_records",
            body="""\
if not pathlib.Path('ATTEMPTED').exists():
    pathlib.Path('ATTEMPTED').write_text('ran')
    raise RuntimeError('setup blew up before any evaluate')
dataset = Dataset(name='d', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
        ),
        "--reruns=1",
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed") == 1
    assert outcomes.get("rerun") == 1

    test = run.test("test_crashes_then_records")
    assert test["outcome"] == "passed"
    assert len(test["cases"]["c"]["attempts"]) == 1, (
        "the crashed invocation must not leave an empty round"
    )
