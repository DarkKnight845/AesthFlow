"""
AGENT_REGISTRY: metadata the Supervisor (and the agents API) uses to discover
and invoke specialist agents without hardcoding imports/dispatch per agent.
"""

from __future__ import annotations

from orchestrator.agents.research_analyst import research_analyst_agent

AGENT_REGISTRY: dict[str, dict[str, object]] = {
    "research_analyst": {
        "name": "Research Analyst",
        "description": "Searches the web and synthesizes findings into researched answers",
        "callable": research_analyst_agent,
        "required_tools": ["web_search", "llm"],
        "supported_tasks": ["research", "investigation", "background"],
    },
}
