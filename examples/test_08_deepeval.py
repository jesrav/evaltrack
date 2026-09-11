"""Example 8: recording a DeepEval eval.

Examples 1 to 6 use pydantic-evals. This one uses [DeepEval], the other runner
evaltrack ships a translator for. Nothing about the marker changes. Hand
`evaltrack.run()` the eval and it translates, records and gates.

DeepEval's `evaluate()` measures without failing anything, which is the shape
evaltrack is built for. The runner scores and evaltrack decides. (DeepEval also
has `assert_test()`, which fails a test itself. evaltrack replaces that gate
rather than adding to it, so do not use both.)

What it shows:

* A `GEval` judge whose `threshold` becomes the case's **bar**. The metric set
  it, so the case gates on it with nothing declared on the marker.
* A metric that sets no `threshold`, which is a **score** and decides nothing
  until `score_bars` gives it a bar. Mind the key. DeepEval reports a `GEval`
  under `"<name> [GEval]"`, and a bar has to name the result as recorded.
* **Naming every case.** Under pytest, DeepEval names any `LLMTestCase` you
  left unnamed after the running test, so a whole dataset arrives under one
  name. evaltrack refuses that rather than merging the cases, because the name
  keys the case's pass-rate history.
* A **sync** test. `evaluate()` is not a coroutine, so it needs no async
  plugin.

The judge is scripted, so this runs offline and deterministically. Pass
`model="gpt-4o-mini"` to `GEval` instead and the eval is unchanged.

    uv run pytest examples/test_08_deepeval.py
    uv run evaltrack ui          # then open the recorded run

`evaluate()` writes its last run to `./.deepeval`, so add that to your
`.gitignore`.

[DeepEval]: https://deepeval.com/
"""

from typing import Any

import pytest
from deepeval.evaluate import evaluate
from deepeval.evaluate.configs import DisplayConfig
from deepeval.metrics import BaseMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams

import evaltrack

# --- the system under test ------------------------------------------------
#
# A scripted assistant stands in for the agent you are evaluating, so the
# example needs no API key and answers the same way every run.

REPLIES = {
    "How do I restore a deleted photo?": (
        "Open Settings > Backups. Deleted photos are restorable for 30 days."
    ),
    "Can I get a refund?": (
        "I can't decide refunds myself. Write to support@snapwombat.example "
        "and the billing team will take a look at your account for you."
    ),
}


# --- the judge ------------------------------------------------------------


class ScriptedJudge(DeepEvalBaseLLM):
    """Replaces the model `GEval` calls, so the example needs no API key.

    DeepEval asks a judge for a filled-in pydantic model rather than for text,
    so answering means building whichever schema the metric passed in.
    """

    def __init__(self, scores: dict[str, int]) -> None:
        self.scores = scores
        super().__init__(model="scripted-judge")

    def load_model(self, *args: Any, **kwargs: Any) -> "ScriptedJudge":
        return self

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return "scripted-judge"

    def generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        if "steps" in getattr(schema, "model_fields", {}):
            return schema(steps=["Read the reply.", "Say how helpful it is."])
        # The prompt carries the case, so a scripted judge can answer per case.
        asked = next(key for key in REPLIES if key in prompt)
        return schema(score=self.scores[asked], reason="scripted judgment")

    async def a_generate(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return self.generate(prompt, schema=schema, **kwargs)


class Brevity(BaseMetric):
    """A score, not a verdict: how far under 40 words the reply came in.

    `threshold=None` is DeepEval's way of saying it gates nothing. The marker's
    `score_bars` below is what turns it into a gate.
    """

    threshold = None

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        words = len(str(test_case.actual_output).split())
        self.score = max(0.0, min(1.0, (40 - words) / 40))
        self.reason = f"{words} words"
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    # DeepEval types BaseMetric.__name__ as Literal['Base Metric'], which no
    # custom metric name can satisfy.
    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return "Brevity"


def helpfulness(judge: ScriptedJudge) -> GEval:
    """An LLM-as-judge metric. Its threshold is the bar the case must clear, and
    DeepEval decides pass or fail against it."""
    return GEval(
        name="Helpfulness",
        criteria="Does the reply tell the user what to do next?",
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.7,
    )


# --- the eval -------------------------------------------------------------


def support_cases() -> list[LLMTestCase]:
    return [
        # Name every case. The name keys the case across runs, and its
        # pass-rate history is kept under that name.
        LLMTestCase(
            name="restore-backup",
            input="How do I restore a deleted photo?",
            actual_output=REPLIES["How do I restore a deleted photo?"],
        ),
        LLMTestCase(
            name="refund-demand",
            input="Can I get a refund?",
            actual_output=REPLIES["Can I get a refund?"],
        ),
    ]


@pytest.mark.evaltrack(score_bars={"Brevity": 0.3}, eval_version="support-v1")
def test_support_replies() -> None:
    """Replies point at the right place, and stay short.

    Both metrics are recorded. `Helpfulness [GEval]` gates on its own
    threshold, and `Brevity` gates only because `score_bars` gave it a bar.
    """
    judge = ScriptedJudge({q: 9 for q in REPLIES})
    evaltrack.run(
        lambda: evaluate(
            test_cases=support_cases(),
            metrics=[helpfulness(judge), Brevity()],
            # DeepEval prints a results table and a Confident AI advert on
            # every call. A test has the recorded run for that.
            display_config=DisplayConfig(
                show_indicator=False, print_results=False, inspect_after_run=False
            ),
        )
    )
