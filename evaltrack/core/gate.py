"""Two refusals that keep the gate decidable.

evaltrack gates on every result, so a round it cannot decide in full is refused.
"""

from difflib import get_close_matches

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound
from evaltrack.core.results import EvaluatorResult


def raise_on_unknown_result_fields(eval_round: EvalRound) -> None:
    """Refuse a result that carries a field it does not declare.

    The field is usually a misspelt `bar=` or `verdict=`. A name evaltrack does
    not know decides nothing, so the case passes on a check that never ran.
    """
    __tracebackhide__ = True
    declared = list(EvaluatorResult.model_fields)
    for attempt in eval_round.attempts:
        for name, result in attempt.results.items():
            unknown = sorted(result.__pydantic_extra__ or {})
            if not unknown:
                continue
            hints = [
                f"did you mean {match[0]!r}?"
                for key in unknown
                if (match := get_close_matches(key, declared, n=1))
            ]
            hint = f" {' '.join(hints)}" if hints else ""
            raise EvalDefinitionError(
                f"evaltrack: the result {name!r} on case {attempt.case_id!r} "
                f"carries {', '.join(map(repr, unknown))}, which EvaluatorResult "
                f"does not declare.{hint} An unknown field gates nothing, so it "
                "is refused rather than recorded. Its fields are "
                f"{', '.join(declared)}."
            )


def raise_unless_every_result_gates(eval_round: EvalRound, nodeid: str) -> None:
    """Refuse a round the gate cannot decide in full.

    A score answers only through a bar. The marker's bars are applied by now,
    so a score still without one has none from either side. An attempt
    that evaluated nothing passes on an empty set of checks, which is the same
    mistake with nothing to name. Runs after the crash report, so an attempt
    with no results here is one that ran and judged nothing.
    """
    __tracebackhide__ = True
    bare = sorted(
        {
            name
            for attempt in eval_round.attempts
            for name, result in attempt.results.items()
            if result.verdict is None
        }
    )
    if bare:
        raise EvalDefinitionError(
            f"evaltrack: {', '.join(bare)} scored without a bar, so nothing in "
            f"{nodeid} judges them and the eval reads as checked where it is "
            "not. Give each one a bar with score_bars={...} on the marker, or "
            "with runner_bar= on the EvaluatorResult in a translator, or stop "
            "recording it."
        )
    # Per attempt, not per case. Under repeats= a case with one evaluated
    # attempt and one empty one passes on the empty half.
    unevaluated = sorted(
        {attempt.case_id for attempt in eval_round.attempts if not attempt.results}
    )
    if unevaluated:
        raise EvalDefinitionError(
            f"evaltrack: {', '.join(unevaluated)} recorded no results, so they "
            "pass whatever they produced. Evaluate them, or drop them from the "
            "dataset."
        )
