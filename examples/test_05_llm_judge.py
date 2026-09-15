"""Example 5: an LLM judge as an assertion and as a score.

The two judge evaluators ask different questions. The assertion asks whether
the reply did the job at all. The score asks how good it was. The third case
passes the first and fails the second, so the test fails and carries an `xfail`.

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

# Every model here is scripted, so a real request is a bug and must raise.
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
    """Grades by two rules. A refusal fails. A reply that only hedges is on topic
    but scores low."""
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
    # The judge asks for structured output, so the grade is a tool call.
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, grade)])


# For real models, pass "openai:gpt-4o-mini" to Agent and to LLMJudge instead,
# set OPENAI_API_KEY, and remove the ALLOW_MODEL_REQUESTS line above.
MODEL = FunctionModel(scripted_assistant)
# One scripted judge serves both LLMJudge evaluators. A real judge reads each
# rubric. This one applies the same rules to both.
JUDGE = FunctionModel(scripted_judge)


class MaxLength(Evaluator[str, str, object]):
    """The reply stays within 300 characters. What code can decide must not cost
    a judge call."""

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
        # The text only, so that the judge grades the reply itself.
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
            # As an assertion. Did the reply do the job at all?
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
            # As a score. How good was it? `score_bars` gates it by this name,
            # and `assertion=False` keeps it a score only.
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
