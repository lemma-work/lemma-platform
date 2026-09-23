"""How this installation talks to the Desktop guest, and what a refusal is.

Its own module for the reason `e2b_config` is: the provider file stays about
behaviour. What is here is decided once at startup and read everywhere, and
the two error types beside it are the whole of the bridge's vocabulary -- a
failure is retryable or it is definitive, and every caller branches on which.
"""

from __future__ import annotations

from dataclasses import dataclass

# The bridge is a local process, so these bound a malfunctioning one rather
# than a hostile one.
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class LocalBridgeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "local_runtime_failed",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LocalBridgeNotFound(LocalBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class LemmaLocalProviderConfig:
    executable: str
    request_timeout_seconds: float = 600
    workspace_memory: str = "2g"
    workspace_cpus: str = "2"
    function_memory: str = "2g"
    function_cpus: str = "4"
    callback_required: bool = False
    callback_url: str | None = None
    callback_health_path: str = "/health"
    callback_timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if not self.executable:
            raise ValueError("managed runtime bridge executable is required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("managed runtime timeout must be positive")


__all__ = [
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "LemmaLocalProviderConfig",
    "LocalBridgeError",
    "LocalBridgeNotFound",
]
