"""The gate as the plugin runs it.

Covers what a failing eval prints, and the reports the gate refuses. Those
are an empty report, colliding case names, dropped labels, and crashed tasks.
"""

from pathlib import Path

import pytest

from .helpers import (
    make_eval_source,
    run_eval_session,
)

pytest_plugins = ["pytester"]


# --- failure output: reasons in, plugin frames out ---


_REASONED_FAILURE_SOURCE = make_eval_source(
    name="test_failing_eval",
    marker='@pytest.mark.evaltrack(score_bars={"quality": 0.8})',
    evaluator="""\
return {
    "correct": EvaluationReason(value=False, reason="said blue, expected red"),
    "quality": EvaluationReason(value=0.42, reason="thin"),
}
""",
    task='return "blue"',
    body="""\
dataset = Dataset(name='d', cases=[Case(name='c', inputs='x')], evaluators=[_Evaluator()])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
)


def test_failure_output_carries_reasons_not_plugin_frames(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A reader meets a failing eval in the terminal first. The output must say
    why each case failed, and must not fill the screen with frames the reader
    cannot act on."""
    result, _ = run_eval_session(pytester, tmp_path, _REASONED_FAILURE_SOURCE)
    result.assert_outcomes(failed=1)

    stdout = result.stdout.str()
    assert "c: correct (said blue, expected red), quality=0.42 below bar 0.8" in stdout
    assert "def assert_cases_passed" not in stdout, (
        "the gate's own frame stays out of the output"
    )


# --- evaluator labels: recorded, never gated ---


_LABEL_SOURCE = make_eval_source(
    name="test_labelled",
    evaluator='return {"ok": True, "sentiment": "positive"}',
)


def test_a_label_fails_the_test_end_to_end(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A string-valued evaluator answers something no gate reads. Recorded, it
    would sit in a run whose every other result decides the test."""
    result, _ = run_eval_session(pytester, tmp_path, _LABEL_SOURCE)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*sentiment answered with a label*"])


# --- empty reports: nothing evaluated fails the test ---


_EMPTY_DATASET_SOURCE = make_eval_source(
    name="test_empty",
    body="""\
# Stands in for a dataset file that loaded or filtered to nothing.
dataset = Dataset(name='empty-d', cases=[])
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task, name='empty-d')))
""",
)


def test_empty_report_fails_the_test(pytester: pytest.Pytester, tmp_path: Path) -> None:
    """A dataset that loads to nothing by mistake has no failing case for the
    gate to catch, so without this check it would always pass."""
    result, _ = run_eval_session(pytester, tmp_path, _EMPTY_DATASET_SOURCE)
    result.assert_outcomes(failed=1)


# --- a case no evaluator judged ---


_UNEVALUATED_CASE_SOURCE = make_eval_source(
    name="test_one_case_is_unevaluated",
    evaluator="return ctx.output == ctx.expected_output",
    task="return 'blue' if x == 'unjudged' else x",
    body="""\
# Only the first case carries an evaluator, so the second produces no result at
# all. Its wrong answer once read as a pass.
dataset = Dataset(
    name='d',
    cases=[
        Case(name='judged', inputs='x', expected_output='x', evaluators=[_Evaluator()]),
        Case(name='unjudged', inputs='unjudged', expected_output='unjudged'),
    ],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
)


def test_a_case_nothing_evaluated_fails_the_test(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """A case that recorded no result passes on an empty set of checks, and the
    judged case beside it silences the whole-eval warning. The run then
    reads green even though one case can answer anything."""
    result, _ = run_eval_session(pytester, tmp_path, _UNEVALUATED_CASE_SOURCE)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*unjudged recorded no results*"])


_NAME_COLLISION_SOURCE = make_eval_source(
    name="test_colliding_cases",
    marker="@pytest.mark.evaltrack(flake_reruns=1)",
    evaluator="return {'ok': ctx.inputs != 'u'}",
    body="""\
# The unnamed case auto-names 'Case 1', which the explicit 'Case 1' collides
# with. The 'u' case fails. Without the guard the rerun splice erased it and
# the test passed green.
dataset = Dataset(
    name='d',
    cases=[Case(inputs='u'), Case(name='Case 1', inputs='n')],
    evaluators=[_Evaluator()],
)
evaltrack.run(lambda: asyncio.run(dataset.evaluate(_task)))
""",
)


def test_case_name_collision_fails_before_recording(
    pytester: pytest.Pytester, tmp_path: Path
) -> None:
    """An explicit case name can collide with the name another case is given by
    position. The rerun splice then dropped the failing case and the gate
    passed. It now fails before the eval runs, so nothing is recorded and
    nothing is spent."""
    result, run = run_eval_session(pytester, tmp_path, _NAME_COLLISION_SOURCE)
    result.assert_outcomes(failed=1)
    assert "INTERNALERROR" not in result.stdout.str()
    assert not run.path.exists(), (
        "the rejection comes before the eval, so no run is written"
    )
