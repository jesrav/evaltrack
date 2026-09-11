"""Example 6: make a faked tool's data visible in the run, via inputs.

A common test setup: the agent calls a tool backed by something external (a
document store, a search index, an API). To test the agent under a known setup,
you swap that backend for a fake with a fixed dataset. Here a pydantic-ai
`FunctionModel` scripts the model's replies too, so the example runs offline
and in CI. The model searches first, then answers from the first hit.

Where must the fake's data live? A fixture or a closure hides it from the
dashboard, so you cannot tell what the agent could retrieve. Put it in
`Case.inputs` instead. `inputs` is the only channel that a task can read (to
build the fake) and that evaltrack records, so the faked dataset shows up in the
run.

The agent and its tool look like production code. They depend on a
`DocumentStore` interface, and the test injects a `FakeDocumentStore` built from
the case input. The answer check is a plain phrase match, not the `LLMJudge` of
example 5. The fake store fixes what a grounded answer says, so a judge adds a
model call and nothing else.

The example needs no API key. `--with` installs the async plugin for this one command.

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

# Every model in this example is scripted. A real request means a bug, so it
# must raise, not reach a provider.
models.ALLOW_MODEL_REQUESTS = False

NO_HITS = "(no matching documents)"


# --- production code: the store interface, the agent's deps, and its tool ---


class DocumentStore(Protocol):
    """The interface the agent's tool depends on. A real index backs it in
    production. A fake backs it in the eval."""

    def search(self, query: str) -> list[str]: ...


@dataclass
class Deps:
    store: DocumentStore


def search_documents(ctx: RunContext[Deps], query: str) -> str:
    """The agent's tool. It talks to the `DocumentStore` interface, and the
    caller decides which implementation backs it."""
    hits = ctx.deps.store.search(query)
    return "\n".join(hits) if hits else NO_HITS


# --- the test doubles: the store, built per case from the input, and the model


@dataclass
class FakeDocumentStore:
    """A `DocumentStore` stand-in over a fixed `{title: text}` map."""

    documents: dict[str, str]

    def search(self, query: str) -> list[str]:
        # Naive keyword search: match any non-trivial query term against the
        # title or text. A real store would do better. This is enough to let the
        # agent find relevant docs from a reasonable query.
        terms = [t for t in query.lower().split() if len(t) > 2]
        return [
            f"{title}: {text}"
            for title, text in self.documents.items()
            if any(term in title.lower() or term in text.lower() for term in terms)
        ]


def scripted_qa_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Stand in for the model. Search with the question as the query, then
    answer from the first hit."""
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


# The swap to a real model is `Agent("openai:gpt-4o-mini", ...)`. A real model
# needs OPENAI_API_KEY, and you must remove the ALLOW_MODEL_REQUESTS line above.
MODEL = FunctionModel(scripted_qa_model)


class DocsInput(BaseModel):
    """The eval input. `question` is what the user asks. `documents` is the data
    the fake store holds. It lives in the input, not in a fixture, so the
    dashboard shows exactly what the agent could search."""

    question: str
    documents: dict[str, str]


@dataclass
class ToolCalled(Evaluator[Any, Any, Any]):
    """Assertion: the agent used `tool_name` rather than its own knowledge.

    Read from the run's message history.
    """

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
    """Assertion for one case: the reply states `fact`, a phrase from that
    case's documents. Written per case, it names the exact information the
    answer must contain. It is stricter than one shared check."""

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
    """The agent must search its document store and answer from what it finds.

    There are two checks. A shared assertion says the agent calls
    `search_documents` rather than guesses. A per-case `AnswerStates` names the
    fact that this case's answer must contain. The agent runs against a
    `FakeDocumentStore` built per case from `DocsInput.documents`, so each
    recorded run shows the exact documents the agent could search.
    """
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
        # Swap the real store for a fake built from the input. In production you
        # pass the real store here.
        deps = Deps(store=FakeDocumentStore(inp.documents))
        return await agent.run(inp.question, deps=deps)

    # Two docs answer the questions and three are unrelated noise, so the recorded
    # input shows the agent had to pick the right ones out of a realistic store.
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
                # Per-case check: the fact specific to this question's answer.
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
        # Shared across all cases: the agent must consult the store, not guess.
        evaluators=[ToolCalled("search_documents")],
    )
    await evaltrack.run_async(dataset.evaluate, qa_task)
