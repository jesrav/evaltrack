"""Example 7: recording an eval runner evaltrack has no translator for.

Examples 1 to 6 use pydantic-evals, which evaltrack ships a translator for, so
they only hand the eval to `evaltrack.run()`. This one uses a made-up runner
instead, which stands in for whatever you already have. It also writes the
translator that a shipped runner comes with.

Two pieces make that work:

* A **translator** reads the runner's own result into evaltrack's model. This
  file registers one. A real one lives in a `conftest.py`.
* `evaltrack.run()` translates, records and gates in one step. A failing
  case fails the test right there.

Everything downstream is the same as for any other runner: the dashboard, the
per-case reliability history, the diffs across runs.

Run it. No API key needed, and no async plugin either, because the eval is
driven by the test rather than awaited.

    uv run pytest examples/test_07_other_eval_runner.py
    uv run evaltrack ui          # then open the recorded run
"""

from dataclasses import dataclass

import pytest

import evaltrack
from evaltrack.translators import register

# --- the eval runner ------------------------------------------------------
#
# Stands in for a real one. What matters is the shape every batch eval runner
# shares: cases in, one scored result per case out.


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
    # This runner grades every metric against its own bar. evaltrack honors it,
    # so the marker declares no score bar.
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


# --- the translator -------------------------------------------------------


RUNNER_NAME = "example-runner"


class RunnerTranslator:
    """Reads `RunnerReport` into evaltrack's model.

    evaltrack dispatches on the type of the object handed to `evaltrack.run`,
    so a project whose tests use two runners needs no annotation on either.
    """

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
                            # The runner's own bar. `score_bars` for the same
                            # name on the marker replaces it.
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


# --- the eval -------------------------------------------------------------


SAMPLES = [
    Sample(id="france", question="capital of France", expected="Paris"),
    Sample(id="japan", question="capital of Japan", expected="Tokyo"),
    # Scores 1/3: two of its three words are not in the expected answer. Under
    # the runner's own 0.6 bar this case fails, which fails the test.
    Sample(id="peru", question="capital of Peru", expected="Lima"),
]


@pytest.mark.xfail(reason="the 'peru' case misses the runner's relevance bar")
@pytest.mark.evaltrack(eval_version="lookup-v1")
def test_capitals() -> None:
    """Answers to capital-city questions stay relevant to the expected answer.

    `evaltrack.run` gates as well as records, so the failing case fails this
    test. The `xfail` above is only so the example suite stays green. Drop it
    and this is an ordinary failing eval.
    """
    evaltrack.run(run_eval, SAMPLES)
