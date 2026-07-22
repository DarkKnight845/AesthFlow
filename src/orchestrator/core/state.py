"""
Shared state schema passed between every node in a LangGraph execution.

Why this matters more than it looks: LangGraph works by threading ONE state
object through every node in the graph. Every node reads from it and writes
back to it. If this schema is too narrow (e.g., you forget to track token
usage), you'll be retrofitting every existing node and agent later to add a
field. So this is deliberately generic enough to serve the orchestrator,
every specialist agent, and every node type (reasoner, sandbox, search,
verifier) without needing agent-specific subclasses yet.

Design choice: using a TypedDict rather than a Pydantic model, because
LangGraph's StateGraph is built around TypedDict + reducers (annotated merge
functions) for how fields get updated across node hops. Pydantic models work
too, but TypedDict is the more idiomatic and lighter-weight choice for
LangGraph state.
"""

from __future__ import annotations

from typing import Annotated, TypedDict
import operator


class AgentState(TypedDict):
    # --- Identity / tracing ---
    run_id: str                     # ties this execution back to a persisted Run row
    task: str                       # the original user/task request, set once at the start

    # --- Orchestration ---
    # Which specialist agent is currently active / was last routed to.
    # The supervisor reads and writes this to decide the next hop.
    current_agent: str | None

    # Running list of (agent_name, result) tuples — how the orchestrator
    # accumulates specialist outputs to merge into a final answer.
    # `operator.add` as a reducer means LangGraph appends rather than
    # overwrites when multiple nodes update this key in the same step.
    agent_results: Annotated[list[dict[str, object]], operator.add]

    # --- Node-level working data ---
    # Scratch space for whatever the currently active node needs to pass to
    # the next node in its own subgraph (e.g., search results into a
    # summarizer). Kept generic (dict) rather than typed per-node, since
    # every node type needs a different shape here.
    scratch: dict[str, object]

    # --- Guardrails ---
    token_count: int                # cumulative tokens used this run so far
    max_tokens: int                 # ceiling pulled from settings.max_tokens_per_run at start

    # --- Termination ---
    is_complete: bool               # supervisor sets this True to end the graph
    final_answer: str | None        # populated once is_complete is True
    error: str | None               # set on hard failure; nodes should check this and short-circuit