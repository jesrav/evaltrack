"""The `evaltrack` marker.

Covers what it records, how it validates its kwargs, and how several markers
on one test resolve.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evaltrack.pytest_plugin import (
    MarkerKwargs,
    _describe_marker_problems,  # pyright: ignore[reportPrivateUsage]
)

from .helpers import (
    DEFAULT_BODY,
    EVAL_TEST_SOURCE,
    FLAKY_ONCE_EVALUATOR,
    FLAKY_ONCE_PREAMBLE,
    find_nodeid,
    indent_block,
    make_eval_source,
    make_score_source,
    read_run,
    run_pytest,
)

pytest_plugins = ["pytester"]


# --- evaltrack marker ---


def test_evaltrack_marker_keys_by_nodeid(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The marker records each `Dataset.evaluate()` call under the test's nodeid,
    so two same-named tests in different modules do not collide."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=EVAL_TEST_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    key = find_nodeid(read_run(out), "test_records_eval")
    assert key == "test_x.py::test_records_eval"


def _make_same_named_eval_source(case: str, inputs: str) -> str:
    """A module whose marked test is always called `test_eval`, so two of them
    differ only by the module they live in."""
    return make_eval_source(
        body=f"""\
dataset = Dataset(
    name='d',
    cases=[Case(name='{case}', inputs='{inputs}', expected_output='{inputs}')],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
    )


def test_same_named_tests_in_different_modules_dont_collide(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """Two tests with the same function name in different modules must produce
    separate entries in the run, not merge into one."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_a=_make_same_named_eval_source("from_a", "x"),
        test_b=_make_same_named_eval_source("from_b", "y"),
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)

    data = read_run(out)
    assert len(data["tests"]) == 2
    keys = [k for k in data["tests"] if k.endswith("::test_eval")]
    assert sorted(keys) == ["test_a.py::test_eval", "test_b.py::test_eval"], (
        "each module's test_eval gets its own nodeid-keyed entry"
    )
    cases_a = data["tests"]["test_a.py::test_eval"]["cases"]
    cases_b = data["tests"]["test_b.py::test_eval"]["cases"]
    assert "from_a" in cases_a
    assert "from_b" in cases_b
    assert "from_b" not in cases_a, "each entry keeps its own cases, unmerged"


_SECOND_EVALUATE_AFTER_RERUN_SOURCE = make_eval_source(
    name="test_two_evaluates",
    marker="@pytest.mark.evaltrack(flake_reruns=2)",
    preamble=FLAKY_ONCE_PREAMBLE,
    evaluator=FLAKY_ONCE_EVALUATOR,
    body="""\
dataset = Dataset(name='d', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))  # rerun loop fires and recovers
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))  # second top-level eval: refused
""",
)


def test_second_evaluate_fails_even_after_reruns_fired(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A test's nodeid is its eval's identity, so a second `Dataset.evaluate()`
    in one marked test must fail.

    It must still fail when the rerun loop recorded in between. The old guard
    compared report names, and the rerun's recording reset it, so the second
    evaluate passed unnoticed and merged two evals into one history.
    """
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=_SECOND_EVALUATE_AFTER_RERUN_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(failed=1)
    assert "a test records one" in result.stdout.str()

    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_two_evaluates")]
    assert test["outcome"] == "errored", "the refused second evaluate errors the test"
    case = test["cases"]["c"]
    assert case["clean_attempts"] == 2, (
        "the first eval keeps its rerun attempts through the refusal"
    )
    assert case["attempts"][0]["outcome"] == "failed", (
        "the failing first attempt is still there"
    )


def test_test_docstring_is_recorded(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """The test function's docstring is recorded as test-level context, with its
    indentation removed. A test with no docstring records None."""
    out = tmp_path / "run.json"
    source = (
        make_eval_source(
            name="test_with_doc",
            body='"""What this eval checks and how its fakes are set up."""\n'
            + DEFAULT_BODY,
        )
        + f"""\

@pytest.mark.evaltrack
def test_without_doc():
{indent_block(DEFAULT_BODY, 4)}
"""
    )
    pytester.makepyfile(test_x=source)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=2)

    data = read_run(out)
    tests = data["tests"]
    assert tests[find_nodeid(data, "test_with_doc")]["docstring"] == (
        "What this eval checks and how its fakes are set up."
    )
    assert tests[find_nodeid(data, "test_without_doc")]["docstring"] is None


def test_no_reports_means_no_file_or_save(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x="def test_no_reports(): assert True",
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert not out.exists()


# --- score bars: the gate evaltrack.run applies ---


def test_score_bar_pass_records_verdict(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A met score bar passes. The bars themselves are recorded next to the
    verdict, so a later reader can see what the case was held to."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=make_score_source(0.85))
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)

    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_scored")]
    assert test["marker"]["score_bars"] == {"quality": 0.8}
    assert test["cases"]["c"]["outcome"] == "passed"


def test_score_bar_failure_fails_the_test_and_is_still_recorded(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A missed score bar fails the test through the gate `evaltrack.run`
    applies, with no assertion in the test body, and the run is still recorded
    with a failing verdict."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=make_score_source(0.5))
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(failed=1)

    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_scored")]
    assert test["cases"]["c"]["outcome"] != "passed"


# --- marker kwarg validation ---


def _read_marker_error(pytester: pytest.Pytester, marker: str) -> str:
    """Run a session with a bad marker and return its collection-error text."""
    pytester.makepyfile(test_x=make_score_source(1.0, marker=marker))
    result = run_pytest(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR, (
        "a bad marker must abort the session at collection"
    )
    return result.stderr.str()


# Each entry is the marker's kwargs and the phrases its error must contain: the
# kwarg at fault and pydantic's message for it. That the session then aborts is
# one test below, not twelve.
_BAD_MARKER_KWARGS = [
    pytest.param(
        dict(score_bar={"quality": 0.8}),
        ["unknown kwarg 'score_bar'", "Did you mean 'score_bars'?"],
        id="misspelled-kwarg",
    ),
    pytest.param(
        dict(flake_reruns="two"),
        ["flake_reruns: ", "valid integer"],
        id="non-int-flake-reruns",
    ),
    # `flake_reruns=True` is a mistake, not 1. A bool is rejected as a non-int.
    pytest.param(
        dict(flake_reruns=True),
        ["flake_reruns: ", "valid integer"],
        id="bool-flake-reruns",
    ),
    pytest.param(
        dict(repeats=0),
        ["repeats: ", "greater than or equal to 1"],
        id="repeats-below-one",
    ),
    # The two knobs pull in opposite directions, so both together are refused.
    pytest.param(
        dict(repeats=3, flake_reruns=1),
        ["one or the other"],
        id="both-directions",
    ),
    pytest.param(
        dict(reliability_target=95),
        ["reliability_target: ", "less than or equal to 1"],
        id="reliability-target-above-one",
    ),
    pytest.param(
        dict(reliability_target=0),
        ["reliability_target: ", "greater than 0"],
        id="reliability-target-at-zero",
    ),
    # NaN would never gate anything, since NaN comparisons are all False.
    pytest.param(
        dict(score_bars={"quality": float("nan")}),
        ["score_bars.quality: ", "finite"],
        id="non-finite-score-bar",
    ),
    # `True` is a mistake, not a bar of 1.0. A bool is rejected here as it is for
    # a round count.
    pytest.param(
        dict(score_bars={"quality": True}),
        ["score_bars.quality: ", "valid number"],
        id="bool-score-bar",
    ),
    # A bad value inside score_bars is reported at its key, not only the field.
    pytest.param(
        dict(score_bars={"quality": "hi"}),
        ["score_bars.quality: ", "valid number"],
        id="bad-nested-score-bar-value",
    ),
    pytest.param(
        dict(score_bars="nope"),
        ["score_bars: ", "dictionary"],
        id="non-dict-score-bars",
    ),
    # Every problem in one marker is reported, not only the first.
    pytest.param(
        dict(flake_reruns=-1, eval_version=3),
        [
            "flake_reruns: ",
            "greater than or equal to 0",
            "eval_version: ",
            "valid string",
        ],
        id="several-problems-at-once",
    ),
]


@pytest.mark.parametrize(("kwargs", "expected_phrases"), _BAD_MARKER_KWARGS)
def test_a_bad_marker_names_the_kwarg_and_the_problem(
    kwargs: dict[str, Any], expected_phrases: list[str]
) -> None:
    with pytest.raises(ValidationError) as caught:
        MarkerKwargs(**kwargs)
    described = _describe_marker_problems(caught.value)
    for phrase in expected_phrases:
        assert phrase in described


def test_a_bad_marker_aborts_the_session_and_names_the_test(
    pytester: pytest.Pytester,
) -> None:
    """The rendering above is checked directly, one case per row. What needs a
    real session is that a bad marker reaches the user at all: it aborts at
    collection rather than being ignored or coerced, and the message says which
    test to fix."""
    err = _read_marker_error(pytester, "@pytest.mark.evaltrack(score_bar={'q': 0.8})")
    assert "test_x.py::test_scored" in err, "the message names the offending test"
    assert "Did you mean 'score_bars'?" in err, "the rendering reaches the user"


def test_a_positional_argument_is_refused(pytester: pytest.Pytester) -> None:
    """Refused before the model sees it, so this one has no rendering to check
    and needs the session."""
    err = _read_marker_error(pytester, "@pytest.mark.evaltrack('v1')")
    assert "no positional arguments" in err


def test_int_score_bar_stays_valid(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """The bool rejection must not reject an int. A bar of `1` means 1.0 and
    gates."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=make_score_source(
            1.0, marker="@pytest.mark.evaltrack(score_bars={'quality': 1})"
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_scored")]
    assert test["marker"]["score_bars"] == {"quality": 1.0}


def test_valid_marker_kwargs_pass_validation(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A marker with every supported kwarg and valid values still runs. The bare
    marker and the single-kwarg cases appear throughout this file."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=make_score_source(
            1.0,
            marker="@pytest.mark.evaltrack(score_bars={'quality': 0.8}, "
            "flake_reruns=1, reliability_target=0.95, eval_version='v1')",
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)
    assert find_nodeid(read_run(out), "test_scored")


# --- several evaltrack markers on one test ---


def _make_chained_marker_source(
    scores: str,
    *,
    module_marker: str,
    test_marker: str,
) -> str:
    """Source for a test with a module-level and a test-level `evaltrack` marker.

    `scores` is the dict literal the single evaluator emits, and every score in
    it needs a bar from one of the markers. A passing assertion comes with it,
    so a test about marker precedence need emit no score at all.
    """
    return make_eval_source(
        name="test_scored",
        marker=test_marker,
        preamble=f"pytestmark = [{module_marker}]",
        evaluator=f"return {{'ok': True, **{scores}}}",
    )


def test_file_level_score_bar_gates_when_test_adds_its_own_marker(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A score bar declared for the whole file must keep gating when a test adds
    an unrelated marker kwarg. Without that, the bar is not applied and a
    failing eval passes."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_chained_marker_source(
            "{'quality': 0.1}",
            module_marker="pytest.mark.evaltrack(score_bars={'quality': 0.8})",
            test_marker="@pytest.mark.evaltrack(eval_version='v1')",
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(failed=1)

    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_scored")]
    assert test["marker"]["score_bars"] == {"quality": 0.8}
    assert test["marker"]["eval_version"] == "v1"
    assert test["cases"]["c"]["outcome"] != "passed"


def test_nearest_marker_wins_per_kwarg(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The docs promise the closest marker wins, and it must win per kwarg. Here
    the test's `eval_version` overrides the file's, and the file's
    `reliability_target` survives because the test never sets it."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_chained_marker_source(
            "{}",
            module_marker="pytest.mark.evaltrack(eval_version='outer', "
            "reliability_target=0.5)",
            test_marker="@pytest.mark.evaltrack(eval_version='inner')",
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)

    data = read_run(out)
    settings = data["tests"][find_nodeid(data, "test_scored")]["marker"]
    assert settings["eval_version"] == "inner"
    assert settings["reliability_target"] == 0.5, (
        "the outer marker's other kwarg survives the override"
    )


def test_nearer_score_bars_replace_the_outer_dict(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """`score_bars` resolves as a whole dict, not key by key, so a test can drop
    a bar its file declared. The dropped bar leaves `quality` unjudged, which
    the gate refuses. A merge would keep the file's bar and pass."""
    out = tmp_path / "run.json"
    pytester.makepyfile(
        test_x=_make_chained_marker_source(
            "{'quality': 0.5, 'safety': 0.5}",
            module_marker="pytest.mark.evaltrack(score_bars={'quality': 0.9})",
            test_marker="@pytest.mark.evaltrack(score_bars={'safety': 0.1})",
        )
    )
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*quality scored without a bar*"])

    data = read_run(out)
    test = data["tests"][find_nodeid(data, "test_scored")]
    assert test["marker"]["score_bars"] == {"safety": 0.1}, (
        "the nearer dict replaces the outer one whole, so a dropped bar stays dropped"
    )


_PARAM_MARKER_SOURCE = make_eval_source(
    name="test_scored",
    args="model",
    marker="""\
@pytest.mark.evaltrack(score_bars={"quality": 0.8})
@pytest.mark.parametrize(
    "model",
    [pytest.param("fancy", marks=pytest.mark.evaltrack(reliability_target=0.95))],
)
""",
    evaluator='return {"quality": 1.0}',
)


def test_param_marker_and_function_marker_are_both_honored(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """The documented pattern puts per-variant config on `pytest.param`, and
    keeps the config shared by every variant on the function. Both must reach
    the eval, or one of them is silently ignored."""
    out = tmp_path / "run.json"
    pytester.makepyfile(test_x=_PARAM_MARKER_SOURCE)
    result = run_pytest(pytester, f"--evaltrack-run-file={out}")
    result.assert_outcomes(passed=1)

    data = read_run(out)
    settings = data["tests"][find_nodeid(data, "test_scored[fancy]")]["marker"]
    assert settings["score_bars"] == {"quality": 0.8}, (
        "the param's own config reaches the eval"
    )
    assert settings["reliability_target"] == 0.95, (
        "the function marker's shared config reaches it too"
    )


def test_bad_kwarg_in_shadowed_marker_fails_collection_loudly(
    pytester: pytest.Pytester,
) -> None:
    """A typo in a file-level marker is easy to miss, because every test in the
    file looks correct. It must still stop the session and name the kwarg, even
    when a test-level marker sits closer."""
    pytester.makepyfile(
        test_x=_make_chained_marker_source(
            "{'quality': 1.0}",
            module_marker="pytest.mark.evaltrack(flake_reruns=3, score_bar={'quality': 0.8})",
            test_marker="@pytest.mark.evaltrack(eval_version='v1')",
        )
    )
    result = run_pytest(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR, (
        "a typo in the file-level marker must still abort the session"
    )
    err = result.stderr.str()
    assert "test_x.py::test_scored" in err
    assert "unknown kwarg 'score_bar'" in err
    assert "Did you mean 'score_bars'?" in err


def test_markers_valid_alone_but_conflicting_together_fail_collection(
    pytester: pytest.Pytester,
) -> None:
    """A file-level `repeats=` and a test-level `flake_reruns=` each pass on their
    own, and only their merge is refused. That refusal must arrive at collection
    as a usage error that names the test and both knobs, since neither marker
    looks wrong by itself."""
    pytester.makepyfile(
        test_x=_make_chained_marker_source(
            "{'quality': 1.0}",
            module_marker="pytest.mark.evaltrack(repeats=2)",
            test_marker="@pytest.mark.evaltrack(flake_reruns=1)",
        )
    )
    result = run_pytest(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR, (
        "a conflict between a test's markers must abort the session at collection"
    )
    err = result.stderr.str()
    assert "test_x.py::test_scored" in err
    assert "repeats=" in err
    assert "flake_reruns=" in err
