"""
RunContext: created once per incoming task, before the LangGraph execution
starts. It's the bridge between "a request came in via the API" and
"here is the initial AgentState the graph will operate on."

Why this is a separate object from AgentState: AgentState is what flows
THROUGH the graph and gets mutated node to node. RunContext is set up ONCE,
before execution, and holds things that never change mid-run (run_id, the
budget ceiling, start time). Keeping them separate avoids accidentally
overwriting immutable run metadata inside a node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

<<<<<<< HEAD
from orchestrator.config import settings
from orchestrator.core.state import AgentState
=======
from src.orchestrator.config import settings
from src.orchestrator.core.state import AgentState
>>>>>>> f80551f (AesthFlow version1.0)


@dataclass
class RunContext:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    task: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_tokens: int = settings.max_tokens_per_run

    def to_initial_state(self) -> AgentState:
        """Builds the starting AgentState dict the graph is invoked with."""
        return AgentState(
            run_id=self.run_id,
            task=self.task,
            current_agent=None,
            agent_results=[],
            scratch={},
            token_count=0,
            max_tokens=self.max_tokens,
            is_complete=False,
            final_answer=None,
            error=None,
        )