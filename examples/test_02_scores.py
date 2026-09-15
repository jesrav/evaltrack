"""Example 2: scores, score bars, and where evaluators attach.

uv run --with pytest-asyncio pytest examples/test_02_scores.py
uv run evaltrack ui
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack


def summarize(text: str) -> str:
    """A toy summarizer that returns the first sentence."""
    first = text.split(".")[0].strip()
    return f"{first}." if first else ""


async def summarize_task(text: str) -> str:
    return summarize(text)


class Compression(Evaluator[str, str, dict[str, str]]):
    """How much shorter the summary is than the input, from 0 to 1.

    A float value makes this a score. A score gates nothing on its own. The
    marker's `score_bars` gives it a bar under the name `compression`.
    """

    def get_default_evaluation_name(self) -> str:
        return "compression"

    def evaluate(
        self, ctx: EvaluatorContext[str, str, dict[str, str]]
    ) -> EvaluationReason:
        n_in, n_out = len(ctx.inputs), len(ctx.output)
        score = 0.0 if n_in == 0 else max(0.0, 1.0 - n_out / n_in)
        return EvaluationReason(
            value=round(score, 2), reason=f"kept {n_out}/{n_in} chars"
        )


class MentionsKeyword(Evaluator[str, str, dict[str, str]]):
    """The summary still contains the key term from `Case.metadata`.

    An evaluator can read the metadata. The task cannot, it gets only the
    inputs. The metadata also shows in the dashboard.
    """

    def get_default_evaluation_name(self) -> str:
        return "mentions_keyword"

    def evaluate(
        self, ctx: EvaluatorContext[str, str, dict[str, str]]
    ) -> EvaluationReason:
        keyword = (ctx.metadata or {}).get("keyword", "")
        present = keyword.lower() in ctx.output.lower()
        return EvaluationReason(
            value=present, reason=f"keyword {keyword!r} present={present}"
        )


class OneSentence(Evaluator[str, str, dict[str, str]]):
    """The summary is one sentence, with exactly one period."""

    def get_default_evaluation_name(self) -> str:
        return "one_sentence"

    def evaluate(
        self, ctx: EvaluatorContext[str, str, dict[str, str]]
    ) -> EvaluationReason:
        n = ctx.output.count(".")
        return EvaluationReason(value=n == 1, reason=f"{n} sentence(s)")


# Every case must drop at least 30% of the text and pass every assertion.
@pytest.mark.evaltrack(score_bars={"compression": 0.30})
@pytest.mark.asyncio
async def test_summarize() -> None:
    """The first-sentence summary must compress the text and keep the key term."""
    dataset = Dataset[str, str](
        name="summarize",
        cases=[
            Case(
                inputs="Python is a popular programming language. It is used everywhere.",
                name="python",
                metadata={"keyword": "Python"},
            ),
            Case(
                inputs="The harbor was busy at dawn. Boats came and went all morning.",
                name="harbor",
                metadata={"keyword": "harbor"},
            ),
            Case(
                inputs="Rome wasn't built in a day. Patience matters. Keep going.",
                name="rome",
                metadata={"keyword": "Rome"},
                # A per-case evaluator runs on this case only.
                evaluators=(OneSentence(),),
            ),
        ],
        # Dataset-level evaluators run on every case.
        evaluators=[Compression(), MentionsKeyword()],
    )
    await evaltrack.run_async(dataset.evaluate, summarize_task)
