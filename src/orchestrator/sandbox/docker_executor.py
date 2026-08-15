"""
DockerSandboxExecutor: runs untrusted Python code in an isolated,
locked-down container and returns its stdout/stderr/exit code.

Security model — every one of these matters, and they're layered
deliberately (defense in depth, not reliance on any single control):

- `network_disabled=True`      — no network access at all, so even
                                  successfully-run malicious code can't
                                  exfiltrate data or download a payload.
- `read_only=True`              — root filesystem is read-only; only /tmp
                                  (a small tmpfs) is writable, so code can't
                                  persist anything or tamper with the image.
- `user="nobody"`                — never runs as root inside the container.
- `cap_drop=["ALL"]`             — strips every Linux capability (no raw
                                  sockets, no ability to change ownership,
                                  no ptrace, etc.).
- `security_opt=["no-new-privileges"]` — blocks any privilege escalation
                                  path even if a setuid binary is present.
- `mem_limit` / `nano_cpus`      — hard resource ceilings, enforced by the
                                  Docker daemon/kernel cgroups, not just
                                  "best effort" in application code.
- Container is destroyed (`remove(force=True)`) immediately after every
  run — nothing persists between executions.

Why timeout enforcement needs its own explanation: docker-py's
`container.wait(timeout=N)` timeout parameter controls the HTTP request
timeout to the Docker daemon, NOT how long the container itself is allowed
to run. Relying on that alone can leave a runaway container still running
on the host even after your Python code has "timed out" and moved on. The
correct fix (used below): submit the blocking wait() call to its own
thread and enforce the timeout at the Python level with
`future.result(timeout=N)`. If that times out, we know the container is
still running and explicitly kill it — this is the only way to guarantee
the container is actually gone at the deadline, not just that our code
stopped waiting for it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound

from orchestrator.core.exceptions import SandboxError, SandboxPolicyViolationError, SandboxTimeoutError
from orchestrator.sandbox.policies import DEFAULT_POLICY, SandboxPolicy, screen_code


class DockerSandboxExecutor:
    def __init__(self, policy: SandboxPolicy | None = None):
        self.policy = policy or DEFAULT_POLICY
        self._client = docker.from_env()

    async def execute(self, code: str) -> dict[str, Any]:
        """Public entry point. Runs the blocking Docker SDK work in a
        thread so it doesn't block the asyncio event loop everything else
        in this codebase depends on."""
        return await asyncio.to_thread(self._execute_sync, code)

    def _execute_sync(self, code: str) -> dict[str, Any]:
        rejection_reason = screen_code(code, self.policy)
        if rejection_reason:
            raise SandboxPolicyViolationError(rejection_reason)

        self._ensure_image_available()

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.py"
            script_path.write_text(code)

            container = None
            try:
                container = self._client.containers.run(
                    image=self.policy.docker_image,
                    command=["python", "/sandbox/script.py"],
                    volumes={tmpdir: {"bind": "/sandbox", "mode": "ro"}},
                    working_dir="/sandbox",
                    network_disabled=self.policy.network_disabled,
                    mem_limit=f"{self.policy.memory_limit_mb}m",
                    nano_cpus=int(self.policy.cpu_limit * 1_000_000_000),
                    user="nobody",
                    read_only=True,
                    tmpfs={"/tmp": "size=64m"},
                    security_opt=["no-new-privileges"],
                    cap_drop=["ALL"],
                    detach=True,
                )

                exit_code = self._wait_with_real_timeout(container)

                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

                return {
                    "exit_code": exit_code,
                    "stdout": stdout[: self.policy.max_output_chars],
                    "stderr": stderr[: self.policy.max_output_chars],
                    "success": exit_code == 0,
                }
            except (SandboxTimeoutError, SandboxPolicyViolationError):
                raise
            except APIError as exc:
                raise SandboxError(f"Docker API error during execution: {exc}") from exc
            finally:
                if container is not None:
                    try:
                        container.remove(force=True)
                    except Exception:  # noqa: BLE001 - best-effort cleanup, never mask the real error
                        pass

    def _wait_with_real_timeout(self, container) -> int:
        """See module docstring for why this can't just be
        `container.wait(timeout=N)`. Submits the blocking wait to its own
        thread and enforces the deadline in Python, killing the container
        explicitly if it's exceeded."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(container.wait)
            try:
                result = future.result(timeout=self.policy.timeout_seconds)
                return result.get("StatusCode", -1)
            except concurrent.futures.TimeoutError as exc:
                container.kill()
                raise SandboxTimeoutError(
                    f"Execution exceeded {self.policy.timeout_seconds}s timeout — container killed"
                ) from exc

    def _ensure_image_available(self) -> None:
        try:
            self._client.images.get(self.policy.docker_image)
        except ImageNotFound:
            # Pulling happens once per host, not per execution — fine to
            # let this block the first-ever run rather than requiring a
            # separate provisioning step, but this is why your very first
            # sandbox execution will be noticeably slower than every one
            # after it.
            self._client.images.pull(self.policy.docker_image)