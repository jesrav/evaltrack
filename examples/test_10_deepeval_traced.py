"""Example 10: scoring the parts of a traced app with DeepEval.

Examples 8 and 9 hand `evaluate()` a list of finished test cases. DeepEval can
also score an app while it runs. Decorate each part with
`@observe(metrics=[...])` and drive `dataset.evals_iterator()` over a dataset of
goldens. Every scored span becomes a case of its own, so one run of the app
produces several.

What it shows:

* **Driving the iterator.** `evals_iterator()` yields goldens and *returns* the
  `EvaluationResult`, so a `for` loop throws the result away. `traced_round()`
  below takes it off the `StopIteration` that ends the generator, which is what
  gives `evaltrack.run()` something to record.
* **Naming every scored span.** A span takes the name of the function it
  decorates, so `retrieve` would arrive twice under one name and evaltrack
  would refuse the round. `update_current_span(name=...)` gives each one a name
  of its own.
* **Scoring the golden's own case.** Each golden produces a case beside the
  spans. The `metrics=` handed to `evals_iterator()` is what scores it. Pass
  none and it reaches evaltrack with no results, and the gate refuses it.

The app is a lookup rather than a model, so this runs offline and answers the
same way every time.

    uv run pytest examples/test_10_deepeval_traced.py
    uv run evaltrack ui          # four cases: two goldens, two spans

`evals_iterator()` writes its last run to `./.deepeval`, so add that to your
`.gitignore`.

[DeepEval]: https://deepeval.com/
"""

from collections.abc import Sequence
from typing import Any

import pytest
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.evaluate.configs import AsyncConfig, DisplayConfig
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from deepeval.tracing import observe, update_current_span, update_current_trace

import evaltrack

# --- the metrics ----------------------------------------------------------
#
# Before the app, because `@observe` builds them as the module is imported.


class FoundADocument(BaseMetric):
    """Scores one `retrieve` span: it came back with something."""

    # DeepEval leaves threshold optional, so `score >= threshold` only type
    # checks once a metric that always has a bar says so.
    threshold: float = 1.0  # pyright: ignore[reportIncompatibleVariableOverride]

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        found = len(test_case.retrieval_context or [])
        self.score = float(found > 0)
        self.success = self.score >= self.threshold
        self.reason = f"retrieved {found} document(s)"
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    # DeepEval types BaseMetric.__name__ as Literal['Base Metric'], which no
    # custom metric name can satisfy.
    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return "Found a document"


class Grounded(BaseMetric):
    """Scores the golden's own case: the answer repeats what was retrieved."""

    threshold: float = 1.0  # pyright: ignore[reportIncompatibleVariableOverride]

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        retrieved = " ".join(str(chunk) for chunk in test_case.retrieval_context or [])
        answered = str(test_case.actual_output or "")
        self.score = float(bool(retrieved) and retrieved in answered)
        self.success = self.score >= self.threshold
        self.reason = "quoted the document" if self.score else "answered from nowhere"
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return "Grounded"


# --- the app under test ---------------------------------------------------
#
# A handbook lookup stands in for a retriever and a model, so the example needs
# no API key.

HANDBOOK = {
    "How do I restore a deleted photo?": (
        "Settings > Backups keeps deleted photos for 30 days."
    ),
    "Where do I see my plan?": "Settings > Account lists your current plan.",
}


# DeepEval ships `observe` untyped, so pyright drops the signature it wraps.
@observe(  # pyright: ignore[reportUntypedFunctionDecorator]
    type="retriever", metrics=[FoundADocument()]
)
def retrieve(question: str, *, case_id: str) -> str:
    """Look the question up, and record what this span retrieved.

    The name is set per golden. Left alone, every golden's retrieval would be
    recorded as `retrieve`, and evaltrack refuses a round in which two cases
    share an id.
    """
    document = HANDBOOK.get(question, "")
    update_current_span(
        name=f"{case_id}/retrieve",
        test_case=LLMTestCase(
            input=question,
            actual_output=document,
            retrieval_context=[document] if document else [],
        ),
    )
    return document


@observe()  # pyright: ignore[reportUntypedFunctionDecorator]
def answer(question: str, *, case_id: str) -> str:
    """The whole app. Its span is the trace, so the golden's case is this one."""
    document = retrieve(question, case_id=case_id)
    reply = (
        f"Here is what the handbook says. {document}"
        if document
        else "I could not find anything about that."
    )
    update_current_trace(
        test_case=LLMTestCase(
            input=question, actual_output=reply, retrieval_context=[document]
        )
    )
    return reply


# --- the eval -------------------------------------------------------------

GOLDENS = [
    # `multimodal` carries a default DeepEval does not expose to a caller.
    Golden(
        name="restore-a-photo",
        input="How do I restore a deleted photo?",
        multimodal=False,
    ),
    Golden(name="find-my-plan", input="Where do I see my plan?", multimodal=False),
]


def traced_round(goldens: Sequence[Golden], *, metrics: list[BaseMetric]) -> Any:
    """One pass over the dataset, returning DeepEval's own result.

    `evals_iterator()` is a generator, so its `EvaluationResult` arrives as the
    value on `StopIteration` rather than as a return value a `for` loop can see.
    """
    dataset = EvaluationDataset(goldens=list(goldens))
    rounds = dataset.evals_iterator(
        metrics=metrics,
        async_config=AsyncConfig(run_async=False),
        # DeepEval prints a results table and a Confident AI advert on every
        # call. A test has the recorded run for that.
        display_config=DisplayConfig(
            show_indicator=False, print_results=False, inspect_after_run=False
        ),
    )
    while True:
        try:
            golden = next(rounds)
        except StopIteration as finished:
            return finished.value
        answer(golden.input, case_id=golden.name or golden.input)


@pytest.mark.evaltrack(eval_version="handbook-v1")
def test_the_app_retrieves_and_stays_grounded() -> None:
    """Four cases from two goldens: each golden's own case, and its `retrieve`
    span.

    `Found a document` is attached to the retriever with `@observe`, so it
    scores a span. `Grounded` goes to `evals_iterator()`, so it scores what the
    whole app produced for each golden.
    """
    evaltrack.run(traced_round, GOLDENS, metrics=[Grounded()])
