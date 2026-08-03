"""
Phase 1 node smoke test: exercises LLMReasonerNode and WebSearchNode
directly, standalone, before any agent wires them together.

Why this exists as its own script rather than waiting for the full Research
Analyst agent checkpoint: if the agent-level checkpoint fails later, you
want to already know these two nodes work correctly in isolation, so any
bug is clearly in the agent's wiring/graph logic, not in the nodes
themselves.

Run with: uv run python scripts/verify_phase1_nodes.py
Prerequisite: docker-compose up -d, alembic upgrade head, and a valid
OPENAI_API_KEY / TAVILY_API_KEY in .env (this script makes real API calls).
"""

import asyncio

from sqlalchemy import select

from orchestrator.core.state import AgentState
from orchestrator.nodes.llm_reasoner import LLMReasonerNode
from orchestrator.nodes.web_search import WebSearchNode
from orchestrator.persistence.db import AsyncSessionLocal
from orchestrator.persistence.models import EventRow, Run


def build_test_state(run_id: str, task: str) -> AgentState:
    """Hand-built state, standing in for what RunContext.to_initial_state()
    would normally produce — no orchestrator/graph involved yet."""
    return AgentState(
        run_id=run_id,
        task=task,
        current_agent=None,
        agent_results=[],
        scratch={},
        token_count=0,
        max_tokens=50_000,
        is_complete=False,
        final_answer=None,
        error=None,
    )


async def create_test_run(task: str) -> str:
    async with AsyncSessionLocal() as session:
        run = Run(task=task, status="running")
        session.add(run)
        await session.commit()
        return run.id


async def assert_events_exist(run_id: str, node_id: str, min_count: int = 1) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EventRow).where(EventRow.run_id == run_id, EventRow.node_id == node_id)
        )
        rows = result.scalars().all()
        assert len(rows) >= min_count, (
            f"Expected at least {min_count} events for node '{node_id}', found {len(rows)}"
        )
        event_types = [row.event_type for row in rows]
        print(f"  events for '{node_id}': {event_types}")


async def test_web_search_node() -> None:
    print("\n--- Testing WebSearchNode ---")
    run_id = await create_test_run("smoke test: web search node")
    state = build_test_state(run_id, task="What is the LangGraph library used for?")

    node = WebSearchNode(node_id="test.web_search")
    result = await node.run(state)

    assert "scratch" in result, "WebSearchNode did not return a 'scratch' update"
    assert "search_results" in result["scratch"], "Expected 'search_results' key in scratch"
    results = result["scratch"]["search_results"]
    assert isinstance(results, list) and len(results) > 0, "Expected at least one search result"
    print(f"  got {len(results)} results, first title: {results[0].get('title')!r}")

    # NODE_STARTED, NODE_COMPLETED, TOOL_CALL, TOOL_RESULT = 4 events expected
    await assert_events_exist(run_id, "test.web_search", min_count=4)
    print("✅ WebSearchNode smoke test passed")


async def test_llm_reasoner_node() -> None:
    print("\n--- Testing LLMReasonerNode ---")
    run_id = await create_test_run("smoke test: llm reasoner node")
    state = build_test_state(run_id, task="Say the word 'pong' and nothing else.")

    node = LLMReasonerNode(
        node_id="test.llm_reasoner",
        system_prompt="You are a terse test assistant. Follow instructions exactly.",
    )
    result = await node.run(state)

    assert "scratch" in result, "LLMReasonerNode did not return a 'scratch' update"
    assert "llm_output" in result["scratch"], "Expected 'llm_output' key in scratch"
    assert result["token_count"] > 0, "Expected token_count to increase after an LLM call"
    print(f"  llm_output: {result['scratch']['llm_output']!r}")
    print(f"  token_count: {result['token_count']}")

    await assert_events_exist(run_id, "test.llm_reasoner", min_count=4)
    print("✅ LLMReasonerNode smoke test passed")


async def test_llm_reasoner_reads_scratch_input() -> None:
    """
    Confirms the node can act as a downstream step reading a PREVIOUS
    node's output, not just the original task — this is the exact pattern
    Research Analyst needs (search results -> summarizer).
    """
    print("\n--- Testing LLMReasonerNode reading from scratch (chained input) ---")
    run_id = await create_test_run("smoke test: chained scratch input")
    state = build_test_state(run_id, task="ignored for this test")
    state["scratch"]["raw_notes"] = "The sky is blue because of Rayleigh scattering."

    node = LLMReasonerNode(
        node_id="test.llm_reasoner_chained",
        system_prompt="Summarize the input in exactly one short sentence.",
        input_key="raw_notes",
        output_key="summary",
    )
    result = await node.run(state)

    assert "summary" in result["scratch"], "Expected 'summary' key — input_key/output_key wiring is broken"
    print(f"  summary: {result['scratch']['summary']!r}")
    print("✅ Chained scratch input test passed")


async def main() -> None:
    await test_web_search_node()
    await test_llm_reasoner_node()
    await test_llm_reasoner_reads_scratch_input()
    print("\n✅ All Phase 1 node smoke tests passed — nodes are ready for agent assembly.")


if __name__ == "__main__":
    asyncio.run(main())