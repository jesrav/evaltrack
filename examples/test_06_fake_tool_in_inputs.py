"""Example 6: making a faked tool's data visible in the run.

The agent's tool reads from a document store that the test fakes. A fixture
would hold the fake's documents where the recorded run cannot see them. Putting
them in `Case.inputs` instead makes them part of the case, so the dashboard
shows what the agent could search.

    uv run --with pytest-asyncio pytest examples/test_06_fake_tool_in_inputs.py
    uv run evaltrack ui
"""

from dataclasses import dataclass
from typing import Any, Protocol

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, models
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

# Every model here is scripted, so a real request is a bug and must raise.
models.ALLOW_MODEL_REQUESTS = False

NO_HITS = "(no matching documents)"


class DocumentStore(Protocol):
    """What the agent's tool depends on. A real index in production, a fake in
    the eval."""

    def search(self, query: str) -> list[str]: ...


@dataclass
class Deps:
    store: DocumentStore


def search_documents(ctx: RunContext[Deps], query: str) -> str:
    """The agent's tool. The caller decides which store backs it."""
    hits = ctx.deps.store.search(query)
    return "\n".join(hits) if hits else NO_HITS


@dataclass
class FakeDocumentStore:
    """A `DocumentStore` over a fixed map of title to text."""

    documents: dict[str, str]

    def search(self, query: str) -> list[str]:
        # A keyword match on the title or the text. Enough for the agent to
        # find the right documents from a reasonable query.
        terms = [t for t in query.lower().split() if len(t) > 2]
        return [
            f"{title}: {text}"
            for title, text in self.documents.items()
            if any(term in title.lower() or term in text.lower() for term in terms)
        ]


def scripted_qa_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Searches with the question as the query, then answers from the first
    hit."""
    latest = messages[-1].parts[-1]
    if isinstance(latest, ToolReturnPart):
        hits = str(latest.content)
        if hits == NO_HITS:
            return ModelResponse(
                parts=[TextPart("I could not find that in our documents.")]
            )
        title, text = hits.splitlines()[0].split(": ", 1)
        return ModelResponse(
            parts=[TextPart(f"{text} That is from our {title} document.")]
        )
    if not isinstance(latest, UserPromptPart):
        raise TypeError(f"unexpected turn: {latest!r}")
    return ModelResponse(
        parts=[ToolCallPart("search_documents", {"query": str(latest.content)})]
    )


# For a real model, pass "openai:gpt-4o-mini" to Agent instead, set
# OPENAI_API_KEY, and remove the ALLOW_MODEL_REQUESTS line above.
MODEL = FunctionModel(scripted_qa_model)


class DocsInput(BaseModel):
    """The eval input. `question` is what the user asks. `documents` is what the
    fake store holds, so that the recorded run shows what the agent could
    search."""

    question: str
    documents: dict[str, str]


@dataclass
class ToolCalled(Evaluator[Any, Any, Any]):
    """The agent called `tool_name` instead of answering from its own
    knowledge."""

    tool_name: str

    def get_default_evaluation_name(self) -> str:
        return f"{self.tool_name}_called"

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        called = any(
            isinstance(part, ToolCallPart) and part.tool_name == self.tool_name
            for message in ctx.output.all_messages()
            for part in message.parts
        )
        return EvaluationReason(
            value=called, reason=f"{self.tool_name} called={called}"
        )


@dataclass
class AnswerStates(Evaluator[Any, Any, Any]):
    """The reply states `fact`, a phrase from the case's documents. One per case,
    so each names exactly what its answer must contain."""

    fact: str

    def get_default_evaluation_name(self) -> str:
        return "answer_grounded"

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        reply = str(ctx.output.output)
        stated = self.fact.lower() in reply.lower()
        return EvaluationReason(value=stated, reason=f"{self.fact!r} stated={stated}")


@pytest.mark.evaltrack
@pytest.mark.asyncio
async def test_document_qa_agent() -> None:
    """The agent must search its document store and answer from what it finds."""
    agent = Agent(
        MODEL,
        deps_type=Deps,
        instructions=(
            "Answer the user's question using the search_documents tool. "
            "Do not answer from your own knowledge."
        ),
        tools=[search_documents],
    )

    async def qa_task(inp: DocsInput) -> Any:
        # A fake store built from the input. In production, pass the real store.
        deps = Deps(store=FakeDocumentStore(inp.documents))
        return await agent.run(inp.question, deps=deps)

    # Two documents answer the questions and three are noise, so the agent has
    # to pick the right ones.
    store = {
        "Refund policy": "Customers may request a refund within 30 days of purchase.",
        "Shipping": "Orders ship within 2 business days.",
        "Gift cards": "Gift cards never expire and work on any order.",
        "Loyalty program": "Members earn one point per dollar spent.",
        "Privacy": "We never sell your personal data to third parties.",
    }
    dataset = Dataset[DocsInput, Any](
        name="document-qa",
        cases=[
            Case(
                inputs=DocsInput(
                    question="What is the refund window?", documents=store
                ),
                name="refund window",
                metadata={"note": "answer should come from the Refund policy doc"},
                # The fact that this answer must state.
                evaluators=(AnswerStates("within 30 days"),),
            ),
            Case(
                inputs=DocsInput(
                    question="How long does shipping take?", documents=store
                ),
                name="shipping time",
                metadata={"note": "answer should come from the Shipping doc"},
                evaluators=(AnswerStates("within 2 business days"),),
            ),
        ],
        # For every case, the agent must consult the store rather than guess.
        evaluators=[ToolCalled("search_documents")],
    )
    await evaltrack.run_async(dataset.evaluate, qa_task)
