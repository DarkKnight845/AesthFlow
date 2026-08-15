"""
Phase 2 sandbox checkpoint: proves the isolation guarantees actually hold,
not just that "normal code runs okay." This is the highest-risk component
in the whole project, so this script deliberately tries to break it.

Run with: uv run python scripts/verify_sandbox.py
Prerequisite: Docker daemon running locally.
"""

import asyncio
import time

from orchestrator.core.exceptions import SandboxPolicyViolationError, SandboxTimeoutError
from orchestrator.sandbox.docker_executor import DockerSandboxExecutor
from orchestrator.sandbox.policies import SandboxPolicy


async def test_normal_execution() -> None:
    print("\n--- Test 1: normal code executes correctly ---")
    executor = DockerSandboxExecutor()
    result = await executor.execute("print('hello from inside the sandbox')")

    assert result["success"], f"Expected success, got: {result}"
    assert "hello from inside the sandbox" in result["stdout"]
    print(f"  stdout: {result['stdout'].strip()!r}")
    print("Normal execution works")


async def test_data_analysis_libraries() -> None:
    print("\n--- Test 2: pandas/numpy actually available in the image ---")
    executor = DockerSandboxExecutor()
    code = (
        "import pandas as pd\n"
        "import numpy as np\n"
        "df = pd.DataFrame({'x': np.arange(5)})\n"
        "print(df['x'].sum())\n"
    )
    result = await executor.execute(code)
    assert result["success"], f"Expected success, got: {result}"
    assert "10" in result["stdout"]
    print(f"  stdout: {result['stdout'].strip()!r}")
    print("Data analysis libraries available and working")


async def test_disallowed_import_rejected_fast() -> None:
    print("\n--- Test 3: blocked import is rejected before a container even starts ---")
    executor = DockerSandboxExecutor()
    start = time.monotonic()
    try:
        await executor.execute("import os\nos.system('echo should never run')")
        raise AssertionError("Expected SandboxPolicyViolationError, but no exception was raised")
    except SandboxPolicyViolationError as exc:
        elapsed = time.monotonic() - start
        print(f"  rejected in {elapsed:.3f}s: {exc}")
        assert elapsed < 2.0, "Rejection took too long - pre-screen may not be short-circuiting correctly"
    print("Disallowed import correctly and quickly rejected")


async def test_timeout_enforced() -> None:
    print("\n--- Test 4: infinite loop is actually killed at the timeout ---")
    policy = SandboxPolicy(timeout_seconds=5)
    executor = DockerSandboxExecutor(policy=policy)
    start = time.monotonic()
    try:
        await executor.execute("while True:\n    pass\n")
        raise AssertionError("Expected SandboxTimeoutError, but the infinite loop did not time out")
    except SandboxTimeoutError as exc:
        elapsed = time.monotonic() - start
        print(f"  killed after {elapsed:.1f}s: {exc}")
        assert 4.0 < elapsed < 15.0, f"Timeout fired at an unexpected time: {elapsed:.1f}s"
    print("Infinite loop was actually killed, not left running")


async def test_network_disabled() -> None:
    print("\n--- Test 5: network access is actually blocked ---")
    executor = DockerSandboxExecutor()
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=3)\n"
        "    print('NETWORK_REACHABLE')\n"
        "except Exception as e:\n"
        "    print(f'NETWORK_BLOCKED: {type(e).__name__}')\n"
    )
    result = await executor.execute(code)
    print(f"  stdout: {result['stdout'].strip()!r}")
    assert "NETWORK_REACHABLE" not in result["stdout"], "Network call succeeded - isolation is broken!"
    assert "NETWORK_BLOCKED" in result["stdout"], "Expected the network call to fail from inside the sandbox"
    print("Network access correctly blocked")


async def test_memory_limit_enforced() -> None:
    print("\n--- Test 6: memory limit is enforced ---")
    policy = SandboxPolicy(memory_limit_mb=64, timeout_seconds=10)
    executor = DockerSandboxExecutor(policy=policy)
    # Attempts to allocate ~500MB, well past the 64MB cap.
    code = "data = bytearray(500 * 1024 * 1024)\nprint('SHOULD_NOT_PRINT')\n"
    result = await executor.execute(code)
    print(f"  exit_code: {result['exit_code']}, stdout: {result['stdout'].strip()!r}")
    assert not result["success"], "Expected the process to fail/be killed due to memory limit"
    assert "SHOULD_NOT_PRINT" not in result["stdout"], "Allocation succeeded - memory limit is not enforced!"
    print("Memory limit correctly enforced (process killed before completing)")


async def main() -> None:
    await test_normal_execution()
    await test_data_analysis_libraries()
    await test_disallowed_import_rejected_fast()
    await test_timeout_enforced()
    await test_network_disabled()
    await test_memory_limit_enforced()
    print("\nAll sandbox adversarial checks passed - isolation guarantees hold.")


if __name__ == "__main__":
    asyncio.run(main())