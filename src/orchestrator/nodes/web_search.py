"""
WebSearchNode: wraps a Tavily search call as a Logic Node.

Design choices:

1. Uses AsyncTavilyClient rather than the sync TavilyClient. Everything
   else in this codebase is async (FastAPI, SQLAlchemy async engine,
   LangGraph's async execution) — mixing in a blocking sync call here
   would block the event loop during every search. If your installed
   tavily-python version doesn't expose AsyncTavilyClient, the fallback
   noted in the comment below (asyncio.to_thread) is the correct fix,
   not switching everything else to sync.

2. Writes results into `state["scratch"]` under a configurable key rather
   than a hardcoded one, same reasoning as LLMReasonerNode: this lets
   Research Analyst and Fact Checker both use WebSearchNode without
   colliding on where results get stored if an agent ever chains two
   searches.

3. Truncates result content before storing it in scratch/state. Full page
   content from several search results can be large, and since AgentState
   flows through the entire graph, bloating it here would show up as
   inflated token usage downstream when the reasoner reads it. Trimming at
   the source keeps the state lean by construction rather than relying on
   every downstream node to be careful.
"""

from __future__ import annotations

from typing import Any

from tavily import AsyncTavilyClient

from orchestrator.config import settings
from orchestrator.core.events import Event, EventType
from orchestrator.core.state import AgentState
from orchestrator.events_bus.publisher import publish_event
from orchestrator.nodes.base import BaseNode
from orchestrator.persistence.db import AsyncSessionLocal

MAX_CONTENT_CHARS_PER_RESULT = 1500


class WebSearchNode(BaseNode):
    def __init__(
        self,
        node_id: str,
        output_key: str = "search_results",
        query_key: str = "task",
        max_results: int = 5,
    ):
        super().__init__(node_id)
        self.output_key = output_key
        self.query_key = query_key  # usually "task", but could read from scratch for a refined query
        self.max_results = max_results
        self.client = AsyncTavilyClient(api_key=settings.tavily_api_key)

    async def execute(self, state: AgentState) -> dict[str, Any]:
        raw_query = state["scratch"].get(self.query_key)
        query = raw_query if isinstance(raw_query, str) and raw_query.strip() else state["task"]
        run_id = state["run_id"]

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_CALL,
                    payload={"tool": "tavily_search", "query": query},
                ),
                session,
            )

        response = await self.client.search(query=query, max_results=self.max_results)

        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": (item.get("content") or "")[:MAX_CONTENT_CHARS_PER_RESULT],
            }
            for item in response.get("results", [])
        ]

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_RESULT,
                    payload={"tool": "tavily_search", "result_count": len(results)},
                ),
                session,
            )

        return {"scratch": {**state["scratch"], self.output_key: results}}