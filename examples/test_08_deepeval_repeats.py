"""Example 8: demanding consistency from a DeepEval eval with `repeats`.

`repeats=N` runs the eval N times and demands that every case passes every
time. `flake_reruns` is the opposite trade, and the marker takes one or the
other. One case here fails one of its three rounds, so the test fails and
carries an `xfail`.

    uv run pytest examples/test_08_deepeval_repeats.py
    uv run evaltrack ui
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
    """Answers the Japan question wrong once in every three tries, so that the
    example fails the same way every run."""
    wrong = next(_rounds) == 0
    return {"france": "Paris", "japan": "Kyoto" if wrong else "Tokyo"}


class ExactMatch(BaseMetric):
    """The answer is the expected one, or it is not."""

    # DeepEval leaves the threshold optional. Declaring it here is what lets
    # `score >= threshold` type check.
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
    """Every case has to pass all three rounds, not only its best one.

    Without the `xfail`, this is an ordinary failing eval. The dashboard shows
    `japan` at 2 of 3, and the pytest failure names the case.
    """
    evaltrack.run(capitals_round)
