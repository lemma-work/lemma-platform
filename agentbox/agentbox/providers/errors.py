from __future__ import annotations


class ProviderError(RuntimeError):
    """Provider-neutral failure surfaced by lifecycle adapters."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: int = 502,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.headers = dict(headers or {})


class SandboxNotFoundError(ProviderError):
    def __init__(self, sandbox_id: str) -> None:
        super().__init__(
            f"Sandbox {sandbox_id} was not found",
            code="sandbox_not_found",
            status_code=404,
        )
