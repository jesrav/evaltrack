"""Example 2: scores, score bars, and where evaluators attach.

Still a plain function and still no API key (see example 1). The point here is
the evaluator mechanics, which work the same on an LLM or agent task:
- An evaluator can return a float: pydantic-evals routes numeric values into
  the case's `scores` (bools go to `assertions`). The marker's `score_bars` then
  gate each case: it passes only if every named score clears its bar.
- Evaluators attach at the dataset level (run on every case) or on a single
  `Case` (run only there).
- `Case.metadata` carries per-case context. Evaluators can read it (the task
  cannot, it only receives `inputs`) and it shows in the dashboard.

No LLM and no API key.

    uv run --with pytest-asyncio pytest examples/test_02_scores.py
    uv run evaltrack ui
"""

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

# --- system under test: a deterministic "summarizer" -----------------------


def summarize(text: str) -> str:
    """Toy summarizer: return the first sentence."""
    first = text.split(".")[0].strip()
    return f"{first}." if first else ""


async def summarize_task(text: str) -> str:
    return summarize(text)


# --- a score evaluator (returns a float) -----------------------------------


class Compression(Evaluator[str, str, dict[str, str]]):
    """Score in [0, 1]: how much shorter the summary is than the input.

    A float `value` lands in the `scores` bucket.
    `get_default_evaluation_name` names it `compression`, so `score_bars` can
    gate it.
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


# --- an assertion evaluator that reads Case.metadata -----------------------


class MentionsKeyword(Evaluator[str, str, dict[str, str]]):
    """Assertion: the summary still contains the case's key term, taken from
    ``Case.metadata['keyword']``. Evaluators see metadata, the task does not."""

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
    """Assertion: the summary is a single sentence (exactly one period)."""

    def get_default_evaluation_name(self) -> str:
        return "one_sentence"

    def evaluate(
        self, ctx: EvaluatorContext[str, str, dict[str, str]]
    ) -> EvaluationReason:
        n = ctx.output.count(".")
        return EvaluationReason(value=n == 1, reason=f"{n} sentence(s)")


# `score_bars` gate the `compression` score: every case must drop at least 30%
# of the text (score >= 0.30). Assertions must also all pass.
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
                # Per-case evaluator: only this case is also checked for being a
                # single sentence.
                evaluators=(OneSentence(),),
            ),
        ],
        # Dataset-level evaluators run on every case.
        evaluators=[Compression(), MentionsKeyword()],
    )
    await evaltrack.run_async(dataset.evaluate, summarize_task)
