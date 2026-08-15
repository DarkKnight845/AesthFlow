"""
SandboxPolicy: resource limits enforced on every code execution, plus a
lightweight pre-execution screen.

Important distinction to hold onto: `blocked_import_patterns` is NOT the
security boundary. It's a best-effort, easily-bypassed string check
(`__import__('os')` sails right past a check for `"import os"`) that exists
purely to fail fast with a clear error message instead of letting obviously
unsafe code run inside the container and fail confusingly. The REAL
security boundary is the container isolation itself, configured in
docker_executor.py: no network access, read-only root filesystem, a
non-root user, all Linux capabilities dropped, and hard resource limits.
Even if a piece of code gets past the pre-screen and does `import os`, it's
running as an unprivileged user in a network-isolated, read-only container
that gets destroyed immediately after — there's nothing meaningful for it
to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.config import settings


@dataclass(frozen=True)
class SandboxPolicy:
    docker_image: str = settings.sandbox_docker_image
    timeout_seconds: int = settings.sandbox_timeout_seconds
    memory_limit_mb: int = settings.sandbox_memory_limit_mb
    cpu_limit: float = 1.0            # fraction of a CPU core
    max_output_chars: int = 10_000    # truncate stdout/stderr before it flows into AgentState
    network_disabled: bool = True

    # Fast-fail pre-screen only — see module docstring. Deliberately
    # narrow: this blocks the most common ways generated code might try to
    # touch the network, spawn processes, or read/write outside the
    # sandbox, without being so aggressive it rejects legitimate data
    # analysis code (pandas/numpy/matplotlib all pass fine).
    blocked_import_patterns: tuple[str, ...] = field(
        default_factory=lambda: (
            "import os",
            "import subprocess",
            "import socket",
            "import shutil",
            "__import__",
        )
    )


DEFAULT_POLICY = SandboxPolicy()


def screen_code(code: str, policy: SandboxPolicy) -> str | None:
    """Returns a rejection reason string if the code matches a blocked
    pattern, or None if it passes the pre-screen. Called before a
    container is even created — cheaper and faster than letting Docker
    spin up just to reject the code."""
    for pattern in policy.blocked_import_patterns:
        if pattern in code:
            return f"Code contains disallowed pattern: '{pattern}'"
    return None