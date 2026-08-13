"""
Research Analyst agent smoke test: exercises research_analyst_agent end to
end (WebSearchNode -> LLMReasonerNode composed together), as opposed to
verify_phase1_nodes.py which tests each node in isolation.

Run with: uv run python scripts/verify_research_analyst.py
Prerequisite: docker-compose up -d, alembic upgrade head, and a valid
GOOGLE_API_KEY / TAVILY_API_KEY in .env (this script makes real API calls).
"""

import asyncio

from sqlalchemy import select

from orchestrator.agents.research_analyst import AGENT_NAME, research_analyst_agent
from orchestrator.core.state import AgentState
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


async def assert_events_exist(run_id: str, agent_name: str, min_count: int = 1) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EventRow).where(EventRow.run_id == run_id, EventRow.agent_name == agent_name)
        )
        rows = result.scalars().all()
        assert len(rows) >= min_count, (
            f"Expected at least {min_count} events for agent '{agent_name}', found {len(rows)}"
        )
        event_types = [row.event_type for row in rows]
        print(f"  agent-level events for '{agent_name}': {event_types}")


async def test_research_analyst_agent() -> None:
    print("\n--- Testing research_analyst_agent (end to end) ---")
    run_id = await create_test_run("smoke test: research analyst agent")
    state = build_test_state(run_id, task="What is the LangGraph library used for?")

    update = await research_analyst_agent(state)

    assert "agent_results" in update, "Agent did not return an 'agent_results' update"
    results = update["agent_results"]
    assert isinstance(results, list) and len(results) == 1, "Expected exactly one agent result"

    result = results[0]
    assert result["agent"] == AGENT_NAME, f"Expected agent name {AGENT_NAME!r}, got {result['agent']!r}"
    # `answer` comes straight from the LLM response's `.content`. Depending on
    # provider/model, that's a plain string (OpenAI, most Gemini models) or a
    # list of content blocks (seen with gemini-3.6-flash) — accept either,
    # just require it's non-empty.
    answer = result.get("answer")
    assert answer, "Expected non-empty 'answer'"
    assert isinstance(answer, (str, list)), f"Expected 'answer' to be str or list, got {type(answer)}"
    assert isinstance(result.get("sources"), list) and len(result["sources"]) > 0, (
        "Expected at least one source"
    )
    assert isinstance(result.get("token_count"), int) and result["token_count"] > 0, (
        "Expected 'token_count' in agent result to be a positive int"
    )
    assert result.get("metadata", {}).get("nodes_used") == ["web_search", "llm_reasoner"], (
        "Expected metadata.nodes_used to record the composed nodes"
    )

    print(f"  answer: {str(result['answer'])[:120]!r}...")
    print(f"  sources: {len(result['sources'])}")
    print(f"  token_count: {result['token_count']}")

    # Agent-level NODE_STARTED + NODE_COMPLETED = 2 events expected
    # (node-level events are attributed to node_id, not agent_name, so
    # they're excluded from this count on purpose).
    await assert_events_exist(run_id, AGENT_NAME, min_count=2)
    print("✅ research_analyst_agent smoke test passed")


async def test_research_analyst_agent_failure_path() -> None:
    """
    Confirms a failure inside the agent (bad node wiring / provider error)
    still emits NODE_FAILED with agent_name set, and re-raises rather than
    swallowing the exception.
    """
    print("\n--- Testing research_analyst_agent failure path ---")
    run_id = await create_test_run("smoke test: research analyst agent failure")
    state = build_test_state(run_id, task="What is the LangGraph library used for?")
    # Corrupt max_tokens so the reasoner node's budget guardrail trips
    # immediately — a cheap, deterministic way to force a failure without
    # mocking the LLM provider.
    state["max_tokens"] = 0

    try:
        await research_analyst_agent(state)
    except Exception as exc:  # noqa: BLE001 - we just need *an* exception to propagate
        print(f"  agent raised as expected: {type(exc).__name__}: {exc}")
    else:
        raise AssertionError("Expected research_analyst_agent to raise when budget is exceeded")

    await assert_events_exist(run_id, AGENT_NAME, min_count=1)
    print("✅ research_analyst_agent failure path test passed")


async def main() -> None:
    await test_research_analyst_agent()
    await test_research_analyst_agent_failure_path()
    print("\n✅ All Research Analyst agent smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
