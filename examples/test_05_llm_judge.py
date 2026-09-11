"""Example 5: LLM-as-judge, assertion vs score.

An assistant answers a prompt, and the reply is checked three ways. Both the
assistant and the judge are pydantic-ai `FunctionModel`s with scripted replies,
so the example runs offline and in CI. The checks:

- a deterministic assertion: the reply is not too long
- an `LLMJudge` assertion, a must-pass guardrail: the reply answers the question
  and stays on topic
- an `LLMJudge` score: a graded 0-1 helpfulness rating, gated by `score_bars`

The judge works as both an assertion and a score only because the two ask
different questions. The assertion asks "did it do the job at all?" (pass or
fail). The score asks "how good was it?" (graded). One property judged both ways
would be redundant. The marker runs the gate: every case must pass the
assertions and clear the bar.

The third case shows the difference. Its reply is on topic, so it passes the
guardrail. But it only hedges, so the judge scores it under the bar and the
test fails. The `xfail` keeps the example suite green.

The example needs no API key. `--with` installs the async plugin for this one command.

    uv run --with pytest-asyncio pytest examples/test_05_llm_judge.py
    uv run evaltrack ui
"""

import pytest
from pydantic_ai import Agent, models
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
    LLMJudge,
)

import evaltrack

# Every model in this example is scripted. A real request means a bug, so it
# must raise, not reach a provider.
models.ALLOW_MODEL_REQUESTS = False


def latest_prompt(messages: list[ModelMessage]) -> str:
    """The text of the latest user turn."""
    latest = messages[-1].parts[-1]
    if not isinstance(latest, UserPromptPart):
        raise TypeError(f"unexpected turn: {latest!r}")
    return str(latest.content)


# One reply per prompt in the dataset. The last one hedges on purpose.
REPLIES = {
    "Hi! What can you help me with?": (
        "Hi! I can answer questions, explain how things work, and help you "
        "think through a problem. What are you working on?"
    ),
    "What's a good first programming language?": (
        "Python is a good first language: the syntax stays out of your way and "
        "there is a huge amount of beginner material. Start with a small script "
        "that does something you actually need."
    ),
    "How do I center a div in CSS?": (
        "There are a few ways to do that. It depends on your layout, so try a "
        "couple and see which one works."
    ),
}


def scripted_assistant(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stand in for the assistant model."""
    return ModelResponse(parts=[TextPart(REPLIES[latest_prompt(messages)])])


def scripted_judge(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stand in for the judge model. It grades by two rules that a real judge
    applies with more nuance. A refusal fails. A reply that only hedges is on
    topic but scores low."""
    prompt = latest_prompt(messages)
    reply = prompt.split("<Output>")[1].split("</Output>")[0].strip().lower()
    if "sorry" in reply:
        grade = {
            "pass": False,
            "score": 0.0,
            "reason": "The reply refuses instead of answering.",
        }
    elif "depends" in reply:
        grade = {
            "pass": True,
            "score": 0.3,
            "reason": "On topic, but it gives the user nothing to act on.",
        }
    else:
        grade = {
            "pass": True,
            "score": 0.9,
            "reason": "Answers the question with a concrete suggestion.",
        }
    # The judge asks its model for structured output, so the grade goes back
    # as a call to the judge's output tool.
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, grade)])


# The swap to real models is `Agent("openai:gpt-4o-mini", ...)` and
# `LLMJudge(model="openai:gpt-4o-mini", ...)`. Real models need OPENAI_API_KEY,
# and you must remove the ALLOW_MODEL_REQUESTS line above.
MODEL = FunctionModel(scripted_assistant)
# One scripted judge serves both judge evaluators below. A real judge reads
# each rubric. This one applies the same rules to both.
JUDGE = FunctionModel(scripted_judge)


class MaxLength(Evaluator[str, str, object]):
    """The reply stays within 300 characters. A check you can write in code must
    not cost a judge call."""

    def get_default_evaluation_name(self) -> str:
        return "within_length"

    def evaluate(self, ctx: EvaluatorContext[str, str, object]) -> EvaluationReason:
        n = len(ctx.output)
        return EvaluationReason(value=n <= 300, reason=f"{n} chars (limit 300)")


@pytest.mark.evaltrack(score_bars={"helpfulness": 0.6})
@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="the 'vague answer' case misses the helpfulness bar", strict=True
)
async def test_assistant_helpfulness() -> None:
    """The assistant must answer helpfully and on topic, and keep the reply
    short."""
    assistant = Agent(
        MODEL,
        instructions="You are a concise, friendly assistant. Answer in one or two sentences.",
    )

    async def assistant_task(prompt: str) -> str:
        # Return the text output (a string) so the LLMJudge grades the reply itself.
        result = await assistant.run(prompt)
        return result.output

    dataset = Dataset[str, str](
        name="assistant-helpfulness",
        cases=[
            Case(inputs="Hi! What can you help me with?", name="greeting"),
            Case(inputs="What's a good first programming language?", name="advice"),
            Case(inputs="How do I center a div in CSS?", name="vague answer"),
        ],
        evaluators=[
            MaxLength(),
            # LLMJudge as an assertion, a must-pass guardrail: did the reply do
            # the job at all? A different question from the score below.
            LLMJudge(
                rubric=(
                    "The reply actually answers the user and stays on topic: it "
                    "does not refuse, deflect, or wander to an unrelated subject."
                ),
                include_input=True,
                model=JUDGE,
                assertion={
                    "evaluation_name": "answers_on_topic",
                    "include_reason": True,
                },
            ),
            # LLMJudge as a score: how good was it? Named `helpfulness` so
            # `score_bars` can gate it. `assertion=False` keeps it score-only.
            LLMJudge(
                rubric=(
                    "Rate how helpful and clear the reply is, from 0.0 (unhelpful) "
                    "to 1.0 (genuinely useful)."
                ),
                include_input=True,
                model=JUDGE,
                score={"evaluation_name": "helpfulness", "include_reason": True},
                assertion=False,
            ),
        ],
    )
    await evaltrack.run_async(dataset.evaluate, assistant_task)
