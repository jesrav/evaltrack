"""Example 3: absorbing a flaky failure with flake reruns.

The task fails its first attempt at each case and passes after, to stand in for
a model that is not deterministic. The test passes because the rerun rescued
it, and the dashboard still shows the case at one pass out of two attempts.

    uv run --with pytest-asyncio pytest examples/test_03_reliability.py
    uv run evaltrack ui
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

# Attempts per input so far. A real eval needs no counter, the model fails on
# its own. This one makes the flakiness reproducible.
_attempts: dict[str, int] = {}


async def shout_task(word: str) -> str:
    _attempts[word] = _attempts.get(word, 0) + 1
    # The first attempt returns the word unchanged and fails the assertion.
    return word.upper() if _attempts[word] > 1 else word


class IsUpper(Evaluator[str, str, object]):
    """The output is uppercase."""

    def get_default_evaluation_name(self) -> str:
        return "is_upper"

    def evaluate(self, ctx: EvaluatorContext[str, str, object]) -> EvaluationReason:
        return EvaluationReason(
            value=ctx.output.isupper(), reason=f"output {ctx.output!r}"
        )


# `flake_reruns=2` reruns the eval while a case is failing, up to twice. A case
# that passed stays passed. `reliability_target` is a target for the dashboard
# and never fails a test. It is 0.5 here because every case fails one attempt in
# two. A real eval sets it near 1. `eval_version` starts a fresh pass-rate
# history when the prompt or the model changes.
@pytest.mark.evaltrack(flake_reruns=2, reliability_target=0.5, eval_version="shout-v1")
@pytest.mark.asyncio
async def test_shout() -> None:
    """Each case fails its first attempt and passes on the rerun."""
    dataset = Dataset[str, str](
        name="shout",
        cases=[
            Case(inputs="hello", name="hello"),
            Case(inputs="world", name="world"),
        ],
        evaluators=[IsUpper()],
    )
    await evaltrack.run_async(dataset.evaluate, shout_task)
