"""
Research Analyst agent: composes WebSearchNode -> LLMReasonerNode into a
specialist that searches the web and synthesizes findings into an answer.

Node-level NODE_STARTED/COMPLETED/FAILED and TOOL_CALL/TOOL_RESULT events are
already emitted by the nodes themselves (BaseNode.run()). This function additionally
emits its own NODE_STARTED/COMPLETED/FAILED with agent_name set, so the
Execution Log shows the agent's lifecycle distinct from its constituent nodes.

Signature matches what LangGraph expects (`async def node(state) -> dict`),
same as BaseNode.run, so `research_analyst_agent` can be passed straight into
graph_builder.py's StateGraph with no adapter code.
"""

from __future__ import annotations

from typing import Any

from orchestrator.core.events import Event, EventType
from orchestrator.core.state import AgentState
from orchestrator.events_bus.publisher import publish_event
from orchestrator.nodes.llm_reasoner import LLMReasonerNode
from orchestrator.nodes.web_search import WebSearchNode
from orchestrator.persistence.db import AsyncSessionLocal

AGENT_NAME = "research_analyst"

SYSTEM_PROMPT = (
    "You are a research analyst. Given a question and a set of web search "
    "results, synthesize a clear, well-sourced answer. Cite specific facts "
    "from the search results rather than relying on general knowledge."
)


def _format_context(task: str, search_results: list[dict[str, object]]) -> str:
    lines = [f"Question: {task}", "", "Search results:"]
    for i, result in enumerate(search_results, start=1):
        lines.append(f"{i}. {result['title']} ({result['url']})\n{result['content']}")
    return "\n\n".join(lines)


async def research_analyst_agent(state: AgentState) -> dict[str, Any]:
    """Search the web, then synthesize findings into a researched answer."""
    run_id = state["run_id"]
    tokens_before = state["token_count"]

    async with AsyncSessionLocal() as session:
        await publish_event(
            Event(run_id=run_id, agent_name=AGENT_NAME, event_type=EventType.NODE_STARTED),
            session,
        )

    try:
        search_node = WebSearchNode(node_id="research_analyst.web_search")
        search_update = await search_node.run(state)
        state = {**state, **search_update}
        search_results = state["scratch"]["search_results"]

        context_text = _format_context(state["task"], search_results)
        state = {**state, "scratch": {**state["scratch"], "research_context": context_text}}

        reasoner_node = LLMReasonerNode(
            node_id="research_analyst.reasoner",
            system_prompt=SYSTEM_PROMPT,
            input_key="research_context",
            output_key="research_answer",
        )
        reasoner_update = await reasoner_node.run(state)
        state = {**state, **reasoner_update}
        answer = state["scratch"]["research_answer"]

        result = {
            "agent_results": [
                {
                    "agent": AGENT_NAME,
                    "answer": answer,
                    "sources": search_results,
                    "token_count": state["token_count"] - tokens_before,
                    "metadata": {"nodes_used": ["web_search", "llm_reasoner"]},
                }
            ]
        }

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    agent_name=AGENT_NAME,
                    event_type=EventType.NODE_COMPLETED,
                    payload={"sources_count": len(search_results)},
                ),
                session,
            )

        return result

    except Exception as exc:
        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    agent_name=AGENT_NAME,
                    event_type=EventType.NODE_FAILED,
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                ),
                session,
            )
        raise
