"""Example 3: absorbing a flaky failure with flake reruns.

A real eval can be flaky because the model is non-deterministic. The same case
passes most runs and fails now and then. To reproduce that on demand you need an
API key and some luck, so the task here fails its first attempt at each case and
passes after.

- `flake_reruns=N` re-runs the whole eval while a case is still unproven, up to
  `N` extra rounds. A case that already passed stays passed, so a one-off miss
  does not fail the build.
- `reliability_target` compares the pooled pass-rate against what you expect,
  in the dashboard only. It never fails a test. It is 0.5 here because every
  case fails one attempt in two by construction. A real eval would set it near 1.
- `eval_version` segments the recorded pass-rate history. Bump it when the
  prompt or model changes so a fresh window starts.

The test passes, because the rerun rescued it. The dashboard still records the
case at one pass out of two attempts. That is the point. Flake reruns hide the
failure from the gate, and the recorded rate tells you the case is shaky.

The rate `reliability_target` compares against pools runs promoted to the
mainline, so a single local run has nothing in the pool yet. See
docs/flakiness.md for how that pool is built.

No API key needed.

    uv run --with pytest-asyncio pytest examples/test_03_reliability.py
    uv run evaltrack ui
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

# Attempts so far per input, for this pytest session. A real eval needs no such
# counter, because the model fails on its own. The counter makes the flakiness
# reproducible, so the example always shows exactly one rerun.
_attempts: dict[str, int] = {}


async def shout_task(word: str) -> str:
    _attempts[word] = _attempts.get(word, 0) + 1
    # First attempt returns the word unchanged, which fails the assertion below.
    return word.upper() if _attempts[word] > 1 else word


class IsUpper(Evaluator[str, str, object]):
    """Assertion: the output is uppercase."""

    def get_default_evaluation_name(self) -> str:
        return "is_upper"

    def evaluate(self, ctx: EvaluatorContext[str, str, object]) -> EvaluationReason:
        return EvaluationReason(
            value=ctx.output.isupper(), reason=f"output {ctx.output!r}"
        )


# `flake_reruns=2` gives each failing case up to two more attempts. `eval_version`
# starts a fresh pass-rate window when the task or its prompt changes.
@pytest.mark.evaltrack(flake_reruns=2, reliability_target=0.5, eval_version="shout-v1")
@pytest.mark.asyncio
async def test_shout() -> None:
    """Each case fails its first attempt and passes when re-run."""
    dataset = Dataset[str, str](
        name="shout",
        cases=[
            Case(inputs="hello", name="hello"),
            Case(inputs="world", name="world"),
        ],
        evaluators=[IsUpper()],
    )
    await evaltrack.run_async(dataset.evaluate, shout_task)
