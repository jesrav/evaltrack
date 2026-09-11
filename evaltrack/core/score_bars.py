"""What the marker's score bars do to a result.

A bar reaches a result from one of two places. The runner sets it when it
builds the result. The marker declares it in `score_bars`, and
`apply_score_bars` copies it onto the result. The marker's wins, because it is
the project's own policy for the test.
"""

from evaltrack.core.errors import EvalDefinitionError
from evaltrack.core.eval_round import EvalRound
from evaltrack.core.results import EvaluatorResult


def raise_on_unmatched_score_bars(
    eval_round: EvalRound, marker_score_bars: dict[str, float]
) -> None:
    """Every `score_bars` entry must name a score some case produced."""
    __tracebackhide__ = True
    produced = {
        name
        for attempt in eval_round.attempts
        for name, result in attempt.results.items()
        if result.is_score
    }
    unmatched = sorted(name for name in marker_score_bars if name not in produced)
    # A crash produces no scores, so every bar looks misnamed. The caller
    # raises the crash after this, and a report about the bars first hides it.
    crashed = any(attempt.errored for attempt in eval_round.attempts)
    if not unmatched or crashed:
        return
    named = ", ".join(sorted(produced)) or "none"
    raise EvalDefinitionError(
        f"score bar(s) {', '.join(unmatched)} name a score no case produced "
        f"(scores produced: {named}). Check the bar key against the name the "
        "evaluator reports its score under."
    )


def apply_score_bars(
    eval_round: EvalRound, marker_score_bars: dict[str, float]
) -> EvalRound:
    """A copy of `eval_round` with each declared bar written onto the result it
    applies to. The bar replaces a threshold the runner set, and the verdict
    that came from it.
    """
    if not marker_score_bars:
        return eval_round
    attempts = [
        attempt.model_copy(
            update={
                "results": {
                    name: _with_bar(result, marker_score_bars.get(name))
                    for name, result in attempt.results.items()
                }
            }
        )
        for attempt in eval_round.attempts
    ]
    return eval_round.model_copy(update={"attempts": attempts})


def _with_bar(result: EvaluatorResult, bar: float | None) -> EvaluatorResult:
    """A bar applies only to a number. On any other value it is left off.

    Rebuilt through validation rather than copied, because the bar that applies
    and the verdict it reaches are both derived there.
    """
    if bar is None or not result.is_score:
        return result
    return EvaluatorResult.model_validate({**dict(result), "marker_bar": bar})
