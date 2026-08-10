"""
Run a single specialist agent directly, without going through the Supervisor
graph (that's Phase 3 — not built yet). Useful for testing an agent's node
composition end-to-end.

Usage:
    python scripts/run_workflow_cli.py --agent research_analyst --task "Research the history of AI"

Requires Postgres + Redis running (docker-compose up -d) and migrations
applied (alembic upgrade head) — events are foreign-keyed to a Run row, so
this script creates one before invoking the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orchestrator.agents.registry import AGENT_REGISTRY  # noqa: E402
from orchestrator.core.run_context import RunContext  # noqa: E402
from orchestrator.persistence.db import AsyncSessionLocal  # noqa: E402
from orchestrator.persistence.models import Run  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single AesthFlow agent directly.")
    parser.add_argument("--task", required=True, help="The task/question to give the agent.")
    parser.add_argument(
        "--agent",
        required=True,
        choices=sorted(AGENT_REGISTRY),
        help="Which registered agent to run.",
    )
    return parser.parse_args()


async def run(agent_name: str, task: str) -> None:
    run_context = RunContext(task=task)
    state = run_context.to_initial_state()

    async with AsyncSessionLocal() as session:
        session.add(Run(id=run_context.run_id, task=task, status="running"))
        await session.commit()

    agent_callable = AGENT_REGISTRY[agent_name]["callable"]

    print(f"Running agent '{agent_name}' (run_id={run_context.run_id})")
    print(f"Task: {task}\n")

    status = "completed"
    error: str | None = None
    result: dict[str, object] | None = None
    try:
        result = await agent_callable(state)
    except Exception as exc:  # noqa: BLE001 - CLI boundary, report and exit non-zero
        status = "failed"
        error = str(exc)
        print(f"Agent failed: {exc}", file=sys.stderr)

    async with AsyncSessionLocal() as session:
        run_row = await session.get(Run, run_context.run_id)
        run_row.status = status
        run_row.error = error
        if result and result.get("agent_results"):
            answer = result["agent_results"][-1].get("answer")
            # Some providers (e.g. Gemini via langchain) return message content
            # as a list of content-block dicts rather than a plain string.
            if isinstance(answer, list):
                answer = "\n".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in answer
                )
            run_row.final_answer = answer
        await session.commit()

    if result is not None:
        print(json.dumps(result, indent=2, default=str))

    if status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.agent, args.task))
