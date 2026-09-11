"""What an evaluator returned, and what the type of its value means.

A bool is a claim. A number needs a bar before it means anything. Nothing else
is a result, because evaltrack gates on every one.
"""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from evaltrack.core.results import EvaluatorInfo, EvaluatorResult


class TestAssertionOrScore:
    """The value's type says which of the two a result is. Nothing stores that
    answer, so nothing can disagree with the value."""

    @pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
    def test_an_assertion_is_not_a_score(self, value: bool) -> None:
        """A bool is an int in Python, so an unordered check would record
        `True` as the score 1.0 and let a bar grade an assertion."""
        result = EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))
        assert result.is_score is False

    @pytest.mark.parametrize("value", [0.5, 3], ids=["float", "int"])
    def test_a_number_is_a_score(self, value: float) -> None:
        result = EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))
        assert result.is_score is True

    @pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
    def test_a_bool_is_an_assertion(self, value: bool) -> None:
        result = EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))
        assert result.is_assertion is True

    @pytest.mark.parametrize("value", [0.5, 3], ids=["float", "int"])
    def test_a_number_is_not_an_assertion(self, value: float) -> None:
        result = EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))
        assert result.is_assertion is False


class TestAValueIsABoolOrANumber:
    """A translator hands over one of the two, and the model keeps the type it
    was handed. Coerced across them, `True` would read as the score 1 and
    `"0.5"` as a number the evaluator never produced."""

    @pytest.mark.parametrize(
        "value",
        [None, "B+", "0.5", Decimal("0.5"), {"score": 0.5}, [0.5], object()],
        ids=[
            "none",
            "word",
            "numeric-string",
            "decimal",
            "mapping",
            "sequence",
            "object",
        ],
    )
    def test_anything_else_is_refused(self, value: Any) -> None:
        """Nothing here can be gated on. A string grade needs a rule about
        which string is the good one, and evaltrack has none. A None or a
        container is a translator's error to report. A Decimal reads as a
        number to a human and as something else to a type check, so accepting
        it would put the answer in doubt."""
        with pytest.raises(ValidationError, match="value"):
            EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))

    def test_a_bool_stays_a_bool(self) -> None:
        result = EvaluatorResult(value=True, evaluator=EvaluatorInfo(name="e"))
        assert result.value is True
        assert result.is_score is False

    def test_an_int_is_stored_as_its_float(self) -> None:
        """One representation for every runner. Left as reported, the same
        score is `3` from one runner and `3.0` from another, and the stored runs
        disagree on a value nobody meant differently.

        `ResultValue` declares no int, so this also proves the coercion runs
        before the field is checked. Without it the int is refused outright.
        """
        result = EvaluatorResult(value=3, evaluator=EvaluatorInfo(name="e"))
        assert type(result.value) is float
        assert (result.value, result.is_score) == (3.0, True)

    def test_a_bool_is_not_widened_along_with_an_int(self) -> None:
        """A bool is an int in Python. Widened, `True` would be the score 1.0."""
        result = EvaluatorResult(value=True, evaluator=EvaluatorInfo(name="e"))
        assert result.value is True

    def test_an_int_survives_a_stored_round_trip_as_a_float(self) -> None:
        result = EvaluatorResult(value=3, evaluator=EvaluatorInfo(name="e"))
        dumped = result.model_dump()
        assert type(dumped["value"]) is float
        assert EvaluatorResult.model_validate(dumped).value == 3.0

    @pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
    def test_a_non_finite_number_is_refused(self, value: float) -> None:
        """A NaN compares False against any bar, and an infinity clears every
        bar, so neither is a score."""
        with pytest.raises(ValidationError, match="finite"):
            EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))


class TestABarNeedsANumber:
    """An assertion already answers for itself, so a bar beside one is a second
    gate that no comparison reaches."""

    def test_a_bar_on_an_assertion_is_refused(self) -> None:
        with pytest.raises(
            ValidationError, match="needs a number, but the value is the assertion"
        ):
            EvaluatorResult(
                value=True, runner_bar=0.5, evaluator=EvaluatorInfo(name="e")
            )


class TestTheVerdictIsDerived:
    """`verdict` is the gate's answer, reached from the value and the bar. A
    caller cannot set one, so no result carries a judgment that its own value
    and bar do not account for."""

    @pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
    def test_a_bool_is_its_own_verdict(self, value: bool) -> None:
        """An assertion is itself the claim about being good enough. It needs no
        bar, and there is nothing else it could mean."""
        result = EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="e"))
        assert result.verdict is value

    @pytest.mark.parametrize(
        ("value", "verdict"), [(0.4, False), (0.6, True)], ids=["under", "over"]
    )
    def test_a_number_reaches_a_verdict_through_its_bar(
        self, value: float, verdict: bool
    ) -> None:
        result = EvaluatorResult(
            value=value, runner_bar=0.5, evaluator=EvaluatorInfo(name="e")
        )
        assert result.verdict is verdict

    def test_a_score_with_no_bar_reaches_no_verdict(self) -> None:
        """A falsy number read as a failure would invent a judgment nobody
        made. The marker's bar has not arrived yet at this point, and the round
        check refuses a recorded result that never gets one."""
        result = EvaluatorResult(value=0.0, evaluator=EvaluatorInfo(name="e"))
        assert result.verdict is None

    @pytest.mark.parametrize("verdict", [True, False], ids=["pass", "fail"])
    def test_a_verdict_a_caller_passes_is_overwritten(self, verdict: bool) -> None:
        """The shape a runner reaches by thresholding its own score. Keeping it
        would leave a result whose failure the bar beside it cannot explain."""
        result = EvaluatorResult(
            value=0.4,
            verdict=verdict,
            runner_bar=0.5,
            evaluator=EvaluatorInfo(name="e"),
        )
        assert result.verdict is False

    def test_a_marker_bar_re_answers_a_verdict_the_runner_bar_reached(self) -> None:
        """The bar that replaces another has to replace its answer too, or the
        case keeps failing at the threshold the marker overruled."""
        judged = EvaluatorResult(
            value=0.6, runner_bar=0.8, evaluator=EvaluatorInfo(name="e")
        )
        assert judged.verdict is False
        replaced = EvaluatorResult.model_validate({**dict(judged), "marker_bar": 0.5})
        assert (replaced.verdict, replaced.bar) == (True, 0.5)
