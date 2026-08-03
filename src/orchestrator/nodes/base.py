"""
BaseNode: the contract every Logic Node (LLM reasoner, web search, sandbox,
verifier) implements.

Why `run()` and `execute()` are two separate methods rather than one:
`run()` is a template method that ALWAYS emits NODE_STARTED / NODE_COMPLETED
/ NODE_FAILED around whatever the node actually does. Subclasses only
implement `execute()` — the actual work — and get event emission for free,
correctly, every time. This is what makes the "every node must emit events"
rule in the README structural rather than a convention someone can forget.

Why `run()` takes only `state` and returns a partial-state dict: this is
the exact signature LangGraph expects for a node function
(`async def node(state) -> dict`). That means `some_node_instance.run` can
be passed directly to `graph.add_node(...)` in Phase 3's graph_builder.py
with zero adapter code in between.

Why `run()` opens its own DB session per event rather than accepting one
as a parameter: LangGraph calls node functions with just the state — it
has no concept of "also thread a DB session through." Opening a short-lived
session per event write keeps this node fully self-contained and directly
usable as a LangGraph node callable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from orchestrator.core.events import Event, EventType
from orchestrator.core.exceptions import NodeExecutionError, OrchestratorError
from orchestrator.core.state import AgentState
from orchestrator.events_bus.publisher import publish_event
from orchestrator.persistence.db import AsyncSessionLocal


class BaseNode(ABC):
    """
    Subclass this and implement `execute()`. Do not override `run()` unless
    you have a very specific reason to change the event-emission contract
    itself.
    """

    def __init__(self, node_id: str):
        # node_id should be unique per node *instance* within a graph, e.g.
        # "web_search" or "research_analyst.search_step" — this is what
        # shows up on every event this node emits, so make it descriptive
        # enough to trace back to a specific step in the Execution Logs
        # later.
        self.node_id = node_id

    @abstractmethod
    async def execute(self, state: AgentState) -> dict[str, Any]:
        """
        Do the actual work. Read whatever this node needs from `state`,
        return a dict of ONLY the keys that should be merged back into the
        shared state (LangGraph merges partial updates — you don't need to
        return the entire state back).
        """
        raise NotImplementedError

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(run_id=run_id, node_id=self.node_id, event_type=EventType.NODE_STARTED),
                session,
            )

        try:
            result = await self.execute(state)
        except OrchestratorError:
            # Already one of our specific, meaningful error types (e.g.
            # BudgetExceededError, SandboxPolicyViolationError) — emit the
            # failure event but let the specific type propagate unchanged,
            # so callers upstream (the orchestrator, the API layer) can
            # branch on exactly what went wrong instead of a generic
            # NodeExecutionError.
            async with AsyncSessionLocal() as session:
                await publish_event(
                    Event(
                        run_id=run_id,
                        node_id=self.node_id,
                        event_type=EventType.NODE_FAILED,
                        payload={"error_type": "orchestrator_error"},
                    ),
                    session,
                )
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see NodeExecutionError
            async with AsyncSessionLocal() as session:
                await publish_event(
                    Event(
                        run_id=run_id,
                        node_id=self.node_id,
                        event_type=EventType.NODE_FAILED,
                        payload={"error": str(exc), "error_type": type(exc).__name__},
                    ),
                    session,
                )
            # Wrapping in NodeExecutionError standardizes what the graph
            # builder (Phase 3) catches for genuinely unexpected failures
            # (an LLM provider network error, a Tavily timeout, etc.). `retryable`
            # defaults True; individual nodes can raise
            # NodeExecutionError(..., retryable=False) directly for hard
            # failures that shouldn't be retried.
            raise NodeExecutionError(str(exc)) from exc

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.NODE_COMPLETED,
                    payload={"result_keys": list(result.keys())},
                ),
                session,
            )

        return result