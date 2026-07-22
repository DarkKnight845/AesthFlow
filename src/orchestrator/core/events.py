"""
Event schema — the unit of observability for the whole system.

Why this exists before any agent code: every node, every tool call, every
LLM token needs to emit one of these. If you define this AFTER building
agents, you end up bolting logging on inconsistently per-node. Defining it
first means every node you write from here on has a contract to satisfy.

This is deliberately decoupled from HOW events get delivered (Redis, DB,
stdout) — that's the job of events_bus/publisher.py. This module only
defines WHAT an event looks like.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
# from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"

    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    LLM_TOKEN = "llm_token"          # only used if/when you add streaming later
    LLM_COMPLETION = "llm_completion"

    SUPERVISOR_DECISION = "supervisor_decision"  # which agent(s) the orchestrator chose to route to


class Event(BaseModel):
    """
    A single, structured, persistable unit of execution history.

    `event_id` is separate from `run_id`: run_id groups all events for one
    execution, event_id uniquely identifies this specific event so it can be
    referenced/deduplicated independently.
    """
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    node_id: str | None = None       # which node emitted this (None for run-level events)
    agent_name: str | None = None    # which specialist agent this event belongs to, if any
    event_type: EventType
    payload: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_redis_dict(self) -> dict[str, str]:
        """
        Redis Streams requires a flat dict of str->str (or bytes). This
        flattens the event for publishing; the Postgres persistence layer
        uses model_dump() directly instead since it doesn't have that
        restriction.
        """
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "node_id": self.node_id or "",
            "agent_name": self.agent_name or "",
            "event_type": self.event_type.value,
            "payload": self.model_dump_json(include={"payload"}),
            "timestamp": self.timestamp.isoformat(),
        }