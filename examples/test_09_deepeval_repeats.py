"""Example 9: demanding consistency from a DeepEval eval with `repeats`.

Example 8 scores each case once. A nondeterministic agent needs more than one
try before you believe a pass, and the marker's `repeats=N` is what demands
that every case passes all N times.

DeepEval's `evaluate()` runs each case once and has no repeat of its own, so
evaltrack supplies the repetition. It calls the eval N times and stacks the
rounds into N **attempts** per case. Nothing in the eval changes, and there is
no second count to set.

    @pytest.mark.evaltrack(repeats=3)     # every case: 3 attempts, all passing

`flake_reruns=N` is the opposite trade, and the marker takes one or the other.
It re-runs only the cases that failed, and one pass settles the case. Use
`repeats` where a lucky pass is not good enough. Use `flake_reruns` where a
flaky judge must not fail the build.

The assistant here answers one case wrong on the first of its three rounds, so
`repeats=3` fails a test that a single round passes. That is the
point: the `xfail` below is only so the example suite stays green.

    uv run pytest examples/test_09_deepeval_repeats.py
    uv run evaltrack ui          # every attempt is recorded, not just the last

[DeepEval]: https://deepeval.com/
"""

from itertools import count
from typing import Any

import pytest
from deepeval.evaluate import evaluate
from deepeval.evaluate.configs import DisplayConfig
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

import evaltrack

REPEATS = 3

QUESTIONS = {"france": "Capital of France?", "japan": "Capital of Japan?"}
ANSWERS = {"france": "Paris", "japan": "Tokyo"}


_rounds = count()


def flaky_assistant() -> dict[str, str]:
    """Answers the Japan question wrong once in every three tries.

    A real agent is nondeterministic for its own reasons. This one is scripted
    so the example fails the same way every run.
    """
    wrong = next(_rounds) == 0
    return {"france": "Paris", "japan": "Kyoto" if wrong else "Tokyo"}


class ExactMatch(BaseMetric):
    """A verdict, not a score: the answer is the expected one or it is not."""

    # DeepEval leaves threshold optional, so `score >= threshold` only type checks
    # once a metric that always has a bar says so.
    threshold: float = 1.0  # pyright: ignore[reportIncompatibleVariableOverride]

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        self.score = float(test_case.actual_output == test_case.expected_output)
        self.reason = f"answered {test_case.actual_output!r}"
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    # DeepEval types BaseMetric.__name__ as Literal['Base Metric'], which no
    # custom metric name can satisfy.
    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return "Exact match"


def capitals_round() -> Any:
    """One round of the eval. evaltrack calls this once per repeat."""
    answers = flaky_assistant()
    return evaluate(
        test_cases=[
            LLMTestCase(
                name=case_id,
                input=QUESTIONS[case_id],
                actual_output=answers[case_id],
                expected_output=ANSWERS[case_id],
            )
            for case_id in QUESTIONS
        ],
        metrics=[ExactMatch()],
        display_config=DisplayConfig(
            show_indicator=False, print_results=False, inspect_after_run=False
        ),
    )


@pytest.mark.xfail(reason="the 'japan' case answers Kyoto on one of its three rounds")
@pytest.mark.evaltrack(repeats=REPEATS, eval_version="capitals-v1")
def test_capitals_are_answered_consistently() -> None:
    """Every case has to pass all three rounds, not just its best one.

    Drop the `xfail` and this is an ordinary failing eval: the dashboard shows
    `japan` at 2/3, and the pytest failure names the case.
    """
    evaltrack.run(capitals_round)
