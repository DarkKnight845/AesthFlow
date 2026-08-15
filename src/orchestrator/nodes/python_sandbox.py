"""
PythonSandboxNode: wraps DockerSandboxExecutor as a Logic Node, same
pattern as WebSearchNode wrapping Tavily.

Design choices:

1. Reads the code to execute from `state["scratch"][code_key]` rather than
   from `state["task"]` directly - the typical flow is "an LLMReasonerNode
   generates code, then THIS node executes it," so the code lives wherever
   the reasoner wrote it (e.g. Data Analyst: reasoner writes to
   scratch["generated_code"], this node reads code_key="generated_code").

2. Does NOT catch SandboxTimeoutError / SandboxPolicyViolationError /
   SandboxError here. All three are OrchestratorError subclasses, and
   BaseNode.run() already has specific handling for that case - it emits
   NODE_FAILED and re-raises the exact exception type unchanged (see
   nodes/base.py). Catching and re-wrapping them here would lose that
   specificity for no benefit.

3. Stores the FULL result dict (stdout, stderr, exit_code, success) in
   scratch, not just stdout - a downstream reasoner summarizing the
   execution needs to see stderr and exit_code too if something failed,
   not just silently get an empty/wrong answer from truncated stdout.
"""

from __future__ import annotations

from typing import Any

from orchestrator.core.events import Event, EventType
from orchestrator.core.exceptions import NodeExecutionError
from orchestrator.core.state import AgentState
from orchestrator.events_bus.publisher import publish_event
from orchestrator.nodes.base import BaseNode
from orchestrator.persistence.db import AsyncSessionLocal
from orchestrator.sandbox.docker_executor import DockerSandboxExecutor
from orchestrator.sandbox.policies import SandboxPolicy


class PythonSandboxNode(BaseNode):
    def __init__(
        self,
        node_id: str,
        code_key: str = "generated_code",
        output_key: str = "execution_result",
        policy: SandboxPolicy | None = None,
    ):
        super().__init__(node_id)
        self.code_key = code_key
        self.output_key = output_key
        self.executor = DockerSandboxExecutor(policy=policy)

    async def execute(self, state: AgentState) -> dict[str, Any]:
        code = state["scratch"].get(self.code_key)
        if not code or not isinstance(code, str):
            raise NodeExecutionError(
                f"No code found in scratch['{self.code_key}'] - did the "
                f"agent generate code before wiring this node?",
                retryable=False,
            )

        run_id = state["run_id"]
        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_CALL,
                    payload={"tool": "python_sandbox", "code_length": len(code)},
                ),
                session,
            )

        # SandboxTimeoutError / SandboxPolicyViolationError / SandboxError
        # deliberately NOT caught here - see module docstring point 2.
        result = await self.executor.execute(code)

        async with AsyncSessionLocal() as session:
            await publish_event(
                Event(
                    run_id=run_id,
                    node_id=self.node_id,
                    event_type=EventType.TOOL_RESULT,
                    payload={
                        "tool": "python_sandbox",
                        "success": result["success"],
                        "exit_code": result["exit_code"],
                    },
                ),
                session,
            )

        return {"scratch": {**state["scratch"], self.output_key: result}}