"""Which cases are still unproven after a round, and how a later round folds back in.

The algorithm itself, where a full pytest session cannot reach the edges
cheaply. The whole-session behavior is covered in `tests/plugin/test_reruns.py`.
"""

import pytest

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.repetition import find_failing_case_ids, merge_attempts

from ..factories import make_attempt, make_crash_round, make_repeat_round

# --- which cases are retried ---


def test_only_the_cases_that_missed_the_gate_are_retried() -> None:
    attempts = [
        make_attempt("ok", assertions={"a": True}),
        make_attempt("bad", assertions={"a": False}),
    ]
    assert find_failing_case_ids(attempts) == ["bad"]


def test_a_missed_score_bar_is_retried() -> None:
    missed = [make_attempt("low", scores={"q": 0.5}, thresholds={"q": 0.8})]
    assert find_failing_case_ids(missed) == ["low"]
    cleared = [make_attempt("low", scores={"q": 0.9}, thresholds={"q": 0.8})]
    assert find_failing_case_ids(cleared) == [], (
        "a score above the bar leaves nothing to retry"
    )


def test_a_missed_runner_threshold_is_retried() -> None:
    """A runner that bars its own metrics gets reruns with nothing on the marker."""
    attempts = [make_attempt("low", scores={"q": 0.5}, thresholds={"q": 0.8})]
    assert find_failing_case_ids(attempts) == ["low"]


def test_a_repeated_case_is_retried_when_any_attempt_missed_the_gate() -> None:
    """Repeated attempts share one case id. The case is retried unless every
    attempt passed, because one failure already means the case is not reliably
    passing."""
    assert find_failing_case_ids(make_repeat_round([True, False]).attempts) == ["c"]
    assert find_failing_case_ids(make_repeat_round([True, True]).attempts) == [], (
        "a case whose every attempt passed is not retried"
    )


# --- splicing the re-run results back in ---


def test_the_rerun_replaces_the_failing_case_and_keeps_the_rest() -> None:
    """A round runs every case again. Only the case that had failed takes its
    new attempt."""
    attempts = [
        make_attempt("ok", assertions={"a": True}),
        make_attempt("bad", assertions={"a": False}),
    ]
    round_attempts = [
        make_attempt("ok", assertions={"a": True}),
        make_attempt("bad", assertions={"a": True}),
    ]

    merged = merge_attempts(attempts, round_attempts)

    assert [a.case_id for a in merged] == ["ok", "bad"]
    assert merged[1].results["a"].value is True


def test_a_retry_that_drops_a_case_id_is_refused() -> None:
    """The callable runs the same dataset every round, so a vanished case means
    the dataset changed under the eval. Keeping the old attempt would hide that."""
    attempts = [
        make_attempt("ok", assertions={"a": True}),
        make_attempt("bad", assertions={"a": False}),
    ]
    round_attempts = [make_attempt("ok", assertions={"a": True})]

    with pytest.raises(EvalDefinitionError, match="missing the first round's ids bad"):
        merge_attempts(attempts, round_attempts)


def test_a_retry_that_adds_a_case_id_is_refused() -> None:
    """A case the first round never had has no slot in the merge, so it would be
    recorded but never gated, and a failure in it would ship green."""
    attempts = [make_attempt("a", assertions={"x": True})]
    round_attempts = [
        make_attempt("a", assertions={"x": True}),
        make_attempt("extra", assertions={"x": False}),
    ]

    with pytest.raises(EvalDefinitionError, match="extra"):
        merge_attempts(attempts, round_attempts)


def test_a_renamed_case_is_reported_with_both_ids() -> None:
    """Drift usually pairs a vanished id with a new one, so the error names both
    sides and a rename reads as a rename."""
    attempts = [make_attempt("bad", assertions={"a": False})]
    round_attempts = [make_attempt("bad@2", assertions={"a": False})]

    with pytest.raises(EvalDefinitionError, match=r"bad@2") as raised:
        merge_attempts(attempts, round_attempts)

    message = str(raised.value)
    assert "bad@2" in message
    assert "bad" in message


def test_a_rerun_that_crashed_replaces_the_verdict_with_the_crash() -> None:
    """The crash is the latest thing known about the case, and an errored
    attempt never passes, so the merged attempts still fail the gate."""
    attempts = [make_attempt("bad", assertions={"a": False})]
    round_attempts = make_crash_round(case_id="bad").attempts

    merged = merge_attempts(attempts, round_attempts)

    assert [a.case_id for a in merged] == ["bad"]
    assert merged[0].errored


def test_a_case_that_has_passed_is_not_un_passed_by_a_later_round() -> None:
    """A re-run of the whole attempts runs a settled case again. The next
    round's bad luck must not take its pass away. Otherwise a rerun adds
    flakiness where it must absorb it."""
    attempts = [make_attempt("c", assertions={"a": True})]
    round_attempts = [make_attempt("c", assertions={"a": False})]

    merged = merge_attempts(attempts, round_attempts)

    assert merged[0].results["a"].value is True


def test_a_case_settled_by_a_score_bar_is_also_left_alone() -> None:
    """Settled means the case passes the gate this test declared. It does not
    only mean that no assertion failed."""
    attempts = [make_attempt("c", scores={"q": 0.9})]
    round_attempts = [make_attempt("c", scores={"q": 0.1})]

    merged = merge_attempts(attempts, round_attempts)

    assert merged[0].results["q"].value == 0.9


def test_an_unsettled_case_still_takes_the_new_attempt() -> None:
    # The bar must sit on the result. A bare number gates nothing, so without
    # the bar the case counts as settled and never takes the new attempt.
    bar = {"q": 0.5}
    attempts = [make_attempt("c", scores={"q": 0.1}, thresholds=bar)]
    round_attempts = [make_attempt("c", scores={"q": 0.9}, thresholds=bar)]

    merged = merge_attempts(attempts, round_attempts)

    assert merged[0].results["q"].value == 0.9
