"""Example 1: the evaltrack mechanics, with no setup.

The task here is a plain function. That is deliberately not a realistic eval.
You unit-test a deterministic function, you do not write an eval for it. It is
here so you can see the moving parts with no API key and nothing to configure:
a task, some `Case`s, an `Evaluator`, and the marker's gate and recording. Real
evals target non-deterministic output from an LLM or an agent (example 4). The
mechanics are the same.

Run it. No API key needed. `--with` installs the async plugin for this one
command.

    uv run --with pytest-asyncio pytest examples/test_01_function.py
    uv run evaltrack ui          # then open the recorded run
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

# --- the system under test: a plain, deterministic function ----------------

LIMIT = 30


def truncate(text: str) -> str:
    """Shorten text to LIMIT characters. Add an ellipsis when the text is cut."""
    if len(text) <= LIMIT:
        return text
    return text[: LIMIT - 1].rstrip() + "…"


async def truncate_task(text: str) -> str:
    # `Dataset.evaluate` awaits the task, so wrap the sync function.
    return truncate(text)


# --- a custom, deterministic evaluator -------------------------------------


class FitsLimit(Evaluator[str, str, object]):
    """Assertion: the output is no longer than `LIMIT`.

    A bool `value` lands this result in the case's `assertions`. The `reason`
    carries the measured length, so the dashboard explains a failure to a reader
    who does not know the rule. Override `get_default_evaluation_name` to give
    the result an explicit name. Without it, the name is the class name.
    """

    def get_default_evaluation_name(self) -> str:
        return "fits_limit"

    def evaluate(self, ctx: EvaluatorContext[str, str, object]) -> EvaluationReason:
        length = len(ctx.output)
        return EvaluationReason(
            value=length <= LIMIT,
            reason=f"{length} chars, limit {LIMIT}",
        )


# A bare marker is the strict gate: every case must pass every assertion. The
# marker also records the run (to ./.evaltrack by default) and raises if the task
# or an evaluator throws.
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
    # The marker runs the gate after evaluate(), so no manual assert is needed.
    await evaltrack.run_async(dataset.evaluate, truncate_task)
