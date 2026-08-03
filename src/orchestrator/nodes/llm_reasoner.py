"""
LLMReasonerNode: wraps a single LLM call as a Logic Node.

Design choices worth calling out:

1. This node is intentionally generic — it's configured with a system
   prompt and input/output keys at construction time, rather than having
   one hardcoded prompt baked in. This is what lets the SAME node class
   serve as "the summarizer step in Research Analyst" AND "the review step
   in Code Reviewer" — the agent assembling it decides its role, this class
   just knows how to call an LLM and track the result.

2. Token accounting happens here, not in the supervisor or the API layer.
   Every LLM call is the only place tokens actually get spent, so this is
   the correct place to update `state["token_count"]` and to enforce
   `MAX_TOKENS_PER_RUN`. Enforcing it centrally here means no individual
   agent can accidentally bypass the budget guardrail.

3. Emits TOOL_CALL before the request and TOOL_RESULT after — separate
   from the NODE_STARTED/NODE_COMPLETED pair that BaseNode.run() already
   emits. NODE_* events mark this node's lifecycle; TOOL_* events mark the
   specific external call within it. This distinction matters once a node
   makes multiple tool calls internally (not this one, but e.g. a more
   complex reasoning node later) — you'd want to see each call separately
   in the Execution Logs, not just "the node ran."
"""

from __future__ import annotations

from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_openai import ChatOpenAI  # temporarily disabled while using Gemini
from pydantic import SecretStr

from orchestrator.config import settings
from orchestrator.core.events import Event, EventType
from orchestrator.core.exceptions import BudgetExceededError
from orchestrator.core.state import AgentState
from orchestrator.events_bus.publisher import publish_event
from orchestrator.nodes.base import BaseNode
from orchestrator.persistence.db import AsyncSessionLocal


def _total_tokens(response) -> int:
    """
    Extract total_tokens from a LangChain model response.

    Different providers attach usage differently: OpenAI populates a plain
    ``usage_metadata`` dict, while Gemini returns a UsageMetadata object. This
    helper hides that difference so the budget guardrail stays provider-agnostic.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0

    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is not None:
            return total
        return usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    total = getattr(usage, "total_tokens", None)
    if total is not None:
        return total
    return getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)


class LLMReasonerNode(BaseNode):
    def __init__(
        self,
        node_id: str,
        system_prompt: str,
        input_key: str = "task",
        output_key: str = "llm_output",
        model: str = "gemini-3.6-flash",
        temperature: float = 0.2,
    ):
        super().__init__(node_id)
        self.model = model
        self.system_prompt = system_prompt
        self.input_key = input_key    # which key in state["scratch"] (or "task") holds the input text
        self.output_key = output_key  # which key in state["scratch"] this node writes its output to
        # self.llm = ChatOpenAI(
        #     model=model,
        #     temperature=temperature,
        #     api_key=SecretStr(settings.openai_api_key),
        # )
        self.llm = ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=SecretStr(settings.google_api_key),
        )

    async def execute(self, state: AgentState) -> dict[str, Any]:
        # Input can come from the original task or from a previous node's
        # scratch output (e.g. web search results) — checking scratch first
        # lets this same node type serve as either an entry point or a
        # downstream summarizer, depending on how the agent wires it.
        raw_input = state["scratch"].get(self.input_key)
        input_text = raw_input if isinstance(raw_input, str) and raw_input.strip() else state["task"]

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": input_text},
        ]

        run_id = state["run_id"]
        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_CALL,
                    payload={"tool": "llm", "model": self.model},
                ),
                session,
            )

        response = await self.llm.ainvoke(messages)

        # usage_metadata is populated by the LangChain chat model on the response;
        # falling back to 0 defensively in case a provider/model doesn't
        # report it, rather than letting a KeyError crash the node.
        tokens_used = _total_tokens(response)

        new_token_count = state["token_count"] + tokens_used
        if new_token_count > state["max_tokens"]:
            async with AsyncSessionLocal() as session:
                await publish_event(
                    Event(
                        run_id=run_id,
                        node_id=self.node_id,
                        event_type=EventType.TOOL_RESULT,
                        payload={"tool": "llm", "budget_exceeded": True, "tokens_used": tokens_used},
                    ),
                    session,
                )
            raise BudgetExceededError(
                f"Run {run_id} exceeded token budget: {new_token_count}/{state['max_tokens']}"
            )

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_RESULT,
                    payload={"tool": "llm", "tokens_used": tokens_used},
                ),
                session,
            )

        return {
            "scratch": {**state["scratch"], self.output_key: response.content},
            "token_count": new_token_count,
        }