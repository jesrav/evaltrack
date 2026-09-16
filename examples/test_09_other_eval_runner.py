"""Example 9: a translator for a runner that evaltrack does not know.

The runner is made up. The translator reads its result into evaltrack's model
and is registered here. A real one lives in a `conftest.py`.

    uv run pytest examples/test_09_other_eval_runner.py
    uv run evaltrack ui
"""

from dataclasses import dataclass

import pytest

import evaltrack
from evaltrack.translators import register

# The runner. What matters is the shape that every batch eval runner shares,
# cases in and one scored result per case out.


@dataclass
class Sample:
    """One case: what goes in, and what a good answer looks like."""

    id: str
    question: str
    expected: str


@dataclass
class SampleResult:
    """What the runner reports back for one case."""

    id: str
    question: str
    expected: str
    answer: str
    relevance: float


@dataclass
class RunnerReport:
    """The runner's own result type, which the translator below reads."""

    results: list[SampleResult]
    # The runner's own bar. The translator passes it on, so the marker
    # declares none.
    relevance_threshold: float


def run_eval(samples: list[Sample]) -> RunnerReport:
    """Answer every sample and score how relevant the answer is."""
    results = [
        SampleResult(
            id=sample.id,
            question=sample.question,
            expected=sample.expected,
            answer=(answer := _answer(sample.question)),
            relevance=_score_relevance(answer, sample.expected),
        )
        for sample in samples
    ]
    return RunnerReport(results=results, relevance_threshold=0.6)


def _answer(question: str) -> str:
    """The system under test. A lookup here, an LLM call in a real eval."""
    return {
        "capital of France": "Paris",
        "capital of Japan": "Tokyo",
        "capital of Peru": "Lima, I think",
    }.get(question, "no idea")


def _score_relevance(answer: str, expected: str) -> float:
    """Share of the answer's words that the expected answer also uses."""
    words = answer.lower().replace(",", "").split()
    wanted = set(expected.lower().split())
    return sum(word in wanted for word in words) / len(words)


RUNNER_NAME = "example-runner"


class RunnerTranslator:
    """Reads a `RunnerReport` into evaltrack's model. evaltrack picks the
    translator by the type of the result, so the test names none."""

    def translate(self, result: RunnerReport) -> evaltrack.EvalRound:
        evaluator = evaltrack.EvaluatorInfo(name="relevance")
        return evaltrack.EvalRound(
            runner=evaltrack.RunnerInfo(name=RUNNER_NAME, version="1.0.0"),
            attempts=[
                evaltrack.RoundAttempt(
                    case_id=sample.id,
                    inputs=sample.question,
                    expected_output=sample.expected,
                    output=sample.answer,
                    results={
                        "relevance": evaltrack.EvaluatorResult(
                            value=sample.relevance,
                            # A `score_bars` entry of the same name would
                            # replace this bar.
                            runner_bar=result.relevance_threshold,
                            evaluator=evaluator,
                        )
                    },
                )
                for sample in result.results
            ],
        )


register(
    RUNNER_NAME,
    native_types=(f"{__name__}.RunnerReport",),
    load=RunnerTranslator,
)


SAMPLES = [
    Sample(id="france", question="capital of France", expected="Paris"),
    Sample(id="japan", question="capital of Japan", expected="Tokyo"),
    # Scores 1/3, since two of its three words are not in the expected answer.
    # That is under the runner's bar of 0.6, so the test fails.
    Sample(id="peru", question="capital of Peru", expected="Lima"),
]


@pytest.mark.xfail(reason="the 'peru' case misses the runner's relevance bar")
@pytest.mark.evaltrack(eval_version="lookup-v1")
def test_capitals() -> None:
    """Answers to capital-city questions stay relevant to the expected answer.

    The `xfail` keeps the example suite green. Without it, this is an ordinary
    failing eval.
    """
    evaltrack.run(run_eval, SAMPLES)
