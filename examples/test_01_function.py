"""Example 1: the parts of an eval, on a plain function.

A deterministic function needs no eval. It is here so that you can see the
parts with nothing to configure. A real eval targets an LLM or an agent.

    uv run --with pytest-asyncio pytest examples/test_01_function.py
    uv run evaltrack ui          # then open the recorded run
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

LIMIT = 30


def truncate(text: str) -> str:
    """Shorten text to LIMIT characters. Add an ellipsis when the text is cut."""
    if len(text) <= LIMIT:
        return text
    return text[: LIMIT - 1].rstrip() + "…"


async def truncate_task(text: str) -> str:
    # `Dataset.evaluate` awaits the task, so wrap the sync function.
    return truncate(text)


class FitsLimit(Evaluator[str, str, object]):
    """The output is no longer than `LIMIT`.

    A bool value makes this an assertion. The reason carries the measured
    length, so the dashboard can explain a failure.
    """

    # Without this, the result is named after the class.
    def get_default_evaluation_name(self) -> str:
        return "fits_limit"

    def evaluate(self, ctx: EvaluatorContext[str, str, object]) -> EvaluationReason:
        length = len(ctx.output)
        return EvaluationReason(
            value=length <= LIMIT,
            reason=f"{length} chars, limit {LIMIT}",
        )


# A bare marker is the strict gate. Every case must pass every assertion. The
# marker also records the run, to ./.evaltrack by default.
@pytest.mark.evaltrack
@pytest.mark.asyncio
async def test_truncate() -> None:
    """truncate() must never return more than LIMIT characters."""
    dataset = Dataset[str, str](
        name="truncate",
        cases=[
            Case(inputs="Short enough", name="under the limit"),
            Case(inputs="Exactly thirty characters here", name="at the limit"),
            Case(
                inputs="A title far too long to fit inside the limit",
                name="over the limit",
            ),
        ],
        evaluators=[FitsLimit()],
    )
    # The marker runs the gate after evaluate(), so the test needs no assert.
    await evaltrack.run_async(dataset.evaluate, truncate_task)
