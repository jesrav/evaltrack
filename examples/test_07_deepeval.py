"""Example 7: recording a DeepEval eval.

The same marker with a different runner. The judge is scripted, so this runs
offline. Pass `model="gpt-4o-mini"` to `GEval` instead for a real one.

    uv run pytest examples/test_07_deepeval.py
    uv run evaltrack ui
"""

from typing import Any

import pytest
from deepeval.evaluate import evaluate
from deepeval.evaluate.configs import DisplayConfig
from deepeval.metrics import BaseMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams

import evaltrack

# A scripted assistant stands in for the agent under evaluation.
REPLIES = {
    "How do I restore a deleted photo?": (
        "Open Settings > Backups. Deleted photos are restorable for 30 days."
    ),
    "Can I get a refund?": (
        "I can't decide refunds myself. Write to support@snapwombat.example "
        "and the billing team will take a look at your account for you."
    ),
}


class ScriptedJudge(DeepEvalBaseLLM):
    """Stands in for the model `GEval` calls. DeepEval asks a judge for a filled-in
    pydantic model rather than for text, so answering means building whichever
    schema the metric passed in."""

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
    """How far under 40 words the reply came in."""

    # A metric with no threshold gates nothing in DeepEval. The marker's
    # `score_bars` is what gives this score a bar.
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
    """An LLM judge. Its threshold is the case's bar, so it gates with nothing
    declared on the marker."""
    return GEval(
        name="Helpfulness",
        criteria="Does the reply tell the user what to do next?",
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=0.7,
    )


def support_cases() -> list[LLMTestCase]:
    # Every case is named. Under pytest, DeepEval names an unnamed case after
    # the running test. evaltrack refuses a dataset that arrives under one
    # name, because the name keys the case's pass-rate history.
    return [
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
    """Replies point at the right place, and stay short."""
    # DeepEval reports a GEval as "<name> [GEval]", so a bar for it would have to
    # use that key. Brevity is recorded under its own name.
    judge = ScriptedJudge({q: 9 for q in REPLIES})
    evaltrack.run(
        lambda: evaluate(
            test_cases=support_cases(),
            metrics=[helpfulness(judge), Brevity()],
            # Silences DeepEval's results table and advert on every call.
            display_config=DisplayConfig(
                show_indicator=False, print_results=False, inspect_after_run=False
            ),
        )
    )
