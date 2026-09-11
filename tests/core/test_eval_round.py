"""What an attempt must carry as its case id, which fields an `EvalRound`
accepts, and the questions an attempt answers about itself.

The id keys a case's pass-rate history across runs, so every attempt has one,
and it is the translator's to give: the core invents none.
"""

import pytest
from pydantic import ValidationError

from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
    group_by_case_id,
)
from evaltrack.core.results import RunnerInfo

from ..factories import make_attempt

_RUNNER = RunnerInfo(name="test-runner")


def _make(attempts: list[RoundAttempt]) -> EvalRound:
    return EvalRound(runner=_RUNNER, attempts=attempts)


class TestCaseIds:
    def test_the_translators_id_is_the_case_id(self) -> None:
        eval_round = _make([RoundAttempt(case_id="fr", inputs="Capital of France?")])
        assert [attempt.case_id for attempt in eval_round.attempts] == ["fr"]

    def test_an_attempt_without_an_id_is_refused(self) -> None:
        """An id built here from the input or the row would look stable and not
        be: a `repr` differs between processes, and a row moves when the
        dataset does. Only the translator knows what the runner called a case."""
        with pytest.raises(ValidationError):
            RoundAttempt(inputs="Capital of France?")  # pyright: ignore[reportCallIssue]

    def test_an_empty_id_is_refused(self) -> None:
        """An empty string would key every such case onto one history."""
        with pytest.raises(ValidationError):
            RoundAttempt(case_id="", inputs="Capital of France?")

    def test_repeats_of_one_case_share_the_id_the_runner_gave_them(self) -> None:
        """Repeat or trial rows from a runner then group as attempts at one
        case, not as several cases."""
        eval_round = _make(
            [RoundAttempt(case_id="q", inputs="q"), RoundAttempt(case_id="q")]
        )
        assert [attempt.case_id for attempt in eval_round.attempts] == ["q", "q"]


class TestUnknownFields:
    """The in-flight model rejects a field it does not declare. The stored
    model does not. Only a translator builds an `EvalRound`, and it builds a
    new one every time. An unknown field is therefore a translator that sets
    something which no longer exists, not a run from a later version."""

    def test_an_unknown_field_on_a_round_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            EvalRound(runner=_RUNNER, stable_case_ids=True)  # pyright: ignore[reportCallIssue]

    def test_an_unknown_field_on_an_attempt_is_refused(self) -> None:
        """Refusing one costs a translator nothing: `details` takes whatever
        the runner reports that this model does not declare."""
        with pytest.raises(ValidationError):
            RoundAttempt(case_id="c", epoch=2)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    "assertions", [{}, {"x": True}], ids=["nothing-survived", "one-true-assertion"]
)
def test_a_crashed_evaluator_denies_the_attempt(assertions: dict[str, bool]) -> None:
    """A crashed evaluator leaves no assertion or score behind. Without this
    guard the remaining checks pass on an empty set, so a judge that never ran
    would pass the case. A surviving True assertion is not enough either,
    because another declared evaluator never ran and the verdict is unknown.
    """
    attempt = make_attempt(
        "c",
        assertions=assertions,
        errors=[AttemptErrorRecord(message="oops", evaluator="MyEvaluator")],
    )
    assert attempt.passed is False


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
def test_a_non_finite_score_is_refused(value: float) -> None:
    """A NaN compares False against every bar, so `value < bar` would let it
    through. The model refuses a non-finite score instead, so the rule never
    sees one. A 0/0-ratio evaluator fails in its translator, not at the gate.
    """
    with pytest.raises(ValidationError):
        make_attempt("c", scores={"q": value})


def test_repeated_attempts_group_under_one_case() -> None:
    """A case run several times in one round is one case with N attempts.
    That is what makes the rule 'every attempt must pass' possible."""
    groups = group_by_case_id([make_attempt("a"), make_attempt("b"), make_attempt("a")])
    assert {k: len(v) for k, v in groups.items()} == {"a": 2, "b": 1}
    assert list(groups) == ["a", "b"], "keys come back in first-seen order"
