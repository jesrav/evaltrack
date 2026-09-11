"""Example 4: evaluating an agent's tool use.

The first example with an agent. The agent has a `get_weather` tool. We check
that it calls the tool only when the question needs it, with the right argument.
A pydantic-ai `FunctionModel` scripts the model's replies, so the example runs
offline and in CI. A weather question gets a `get_weather` call. Anything else
gets a direct reply.

The tool returns canned data, because the eval tests the model's decision and
not the tool. The agent, the tool and the evaluators are what you write for a
real model. Only the model is a stand-in, one line away from a real one.

The example needs no API key. `--with` installs the async plugin for this one command.

    uv run --with pytest-asyncio pytest examples/test_04_agent_tool_use.py
    uv run evaltrack ui
"""

import re
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic_ai import Agent, models
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

import evaltrack

# Every model in this example is scripted. A real request means a bug, so it
# must raise, not reach a provider.
models.ALLOW_MODEL_REQUESTS = False


def get_weather(city: str) -> str:
    """Return the current weather for a city (stubbed for the eval)."""
    return f"It is 18 degrees and sunny in {city}."


def scripted_weather_model(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    """Stand in for the model. Call `get_weather` for a weather question, and
    answer anything else directly."""
    latest = messages[-1].parts[-1]
    if isinstance(latest, ToolReturnPart):
        # Second turn: the tool answered, so relay its report.
        return ModelResponse(parts=[TextPart(f"Here is the latest: {latest.content}")])
    if not isinstance(latest, UserPromptPart):
        raise TypeError(f"unexpected turn: {latest!r}")
    prompt = str(latest.content)
    city = re.search(r"\bin ([A-Z]\w+)", prompt)
    if "weather" in prompt.lower() and city:
        return ModelResponse(
            parts=[ToolCallPart("get_weather", {"city": city.group(1)})]
        )
    return ModelResponse(
        parts=[
            TextPart(
                "Hi! I am doing well, thanks. Ask me about the weather anywhere "
                "and I will look it up for you."
            )
        ]
    )


# The swap to a real model is `Agent("openai:gpt-4o-mini", ...)`. A real model
# needs OPENAI_API_KEY, and you must remove the ALLOW_MODEL_REQUESTS line above.
MODEL = FunctionModel(scripted_weather_model)


@dataclass
class ToolCalled(Evaluator[Any, Any, Any]):
    """Assertion: the agent called `tool_name`, and passed `args` if given.

    The arguments matter. A check for the tool name alone passes even when the
    agent asked about the wrong city.
    """

    tool_name: str
    args: dict[str, Any] | None = None

    def get_default_evaluation_name(self) -> str:
        # Name the result after the tool, since this evaluator is parameterized.
        return f"{self.tool_name}_called"

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        calls = [
            part.args_as_dict()
            for message in ctx.output.all_messages()
            for part in message.parts
            if isinstance(part, ToolCallPart) and part.tool_name == self.tool_name
        ]
        if not calls:
            return EvaluationReason(value=False, reason=f"{self.tool_name} not called")
        if self.args is None:
            return EvaluationReason(value=True, reason=f"{self.tool_name} called")
        ok = any(all(call.get(k) == v for k, v in self.args.items()) for call in calls)
        return EvaluationReason(
            value=ok, reason=f"{self.tool_name} called with {calls}, wanted {self.args}"
        )


class NoToolCalled(Evaluator[Any, Any, Any]):
    """Assertion: the agent answered without calling any tool."""

    def get_default_evaluation_name(self) -> str:
        return "no_tool_called"

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        any_tool = any(
            isinstance(part, ToolCallPart)
            for message in ctx.output.all_messages()
            for part in message.parts
        )
        return EvaluationReason(
            value=not any_tool, reason=f"any tool called={any_tool}"
        )


@pytest.mark.evaltrack
@pytest.mark.asyncio
async def test_weather_agent() -> None:
    """The agent must call `get_weather` only when the question needs it."""
    agent = Agent(
        MODEL,
        instructions="Use the get_weather tool to answer weather questions.",
        tools=[get_weather],
    )

    async def agent_task(prompt: str) -> Any:
        # Return the full result so evaluators can inspect which tools were called.
        return await agent.run(prompt)

    dataset = Dataset[str, Any](
        name="weather-agent",
        cases=[
            Case(
                inputs="What's the weather in Paris right now?",
                name="weather question",
                # Per-case evaluator: this case must use the tool, and ask
                # about the city in the question.
                evaluators=(ToolCalled("get_weather", {"city": "Paris"}),),
            ),
            Case(
                inputs="Hi there! How are you?",
                name="greeting",
                # This case must not call any tool.
                evaluators=(NoToolCalled(),),
            ),
        ],
    )
    await evaltrack.run_async(dataset.evaluate, agent_task)
