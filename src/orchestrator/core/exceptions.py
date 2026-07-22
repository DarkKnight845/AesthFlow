"""
Custom exceptions, defined up front so every later phase raises/catches
consistent, specific errors instead of bare Exception.
"""


class OrchestratorError(Exception):
    """Base class for all orchestrator-specific errors."""


class BudgetExceededError(OrchestratorError):
    """Raised when a run exceeds MAX_TOKENS_PER_RUN. Hard failure — not retryable."""


class SandboxError(OrchestratorError):
    """Base class for sandbox execution failures."""


class SandboxTimeoutError(SandboxError):
    """Code execution exceeded SANDBOX_TIMEOUT_SECONDS."""


class SandboxPolicyViolationError(SandboxError):
    """Code attempted a disallowed operation (import, syscall, etc.)."""


class AgentNotFoundError(OrchestratorError):
    """Supervisor tried to route to an agent name not present in the registry."""


class NodeExecutionError(OrchestratorError):
    """A node failed during execution. Transient by default — may be retryable."""

    retryable: bool
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable