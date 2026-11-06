"""The answers connector tools give the model when something did not run.

Data rather than a raised error: a connector failure is information the
model can act on -- a wrong argument, an account to connect -- not a reason to
end the run.
"""

from __future__ import annotations

from typing import Any

from app.core.domain.errors import DomainError
from app.core.redaction import redact_text
from app.modules.agent.infrastructure.tools.tool_errors import safe_error_text

#: What a connector error's details may carry back to the model. Connector
#: errors already pass theirs through an allowlist and redaction; this is the
#: same short list again, so a domain error from elsewhere cannot widen it.
_DETAIL_KEYS = (
    "operation_name",
    "upstream_message",
    "upstream_status",
    "server_host",
    "reason",
    "retry_after",
    # The remedy for a connector that cannot run yet: which one, and where its
    # person connects or reconnects.
    "pod_connector",
    "connect_url",
)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A failure the model can act on, rather than one that ends the run.

    `success: False` is the uniform contract `result_is_failure` reads. Without
    it every connector failure counted as a successful tool call -- in spans,
    and in the `isError` an MCP client sees.
    """
    return {"success": False, "error": message, "code": code, **extra}


def _failure(
    exc: DomainError,
    *,
    connector: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    """A connector error, naming what failed and what the provider said.

    The error types fix their own message ("provider is temporarily
    unavailable"), so the text alone named neither the install, the operation,
    the server nor anything the server had said -- the parts a model needs to
    fix its call or tell the person which connector is down.
    """
    details = exc.details if isinstance(exc.details, dict) else {}
    carried = {
        key: redact_text(value) if isinstance(value, str) else value
        for key in _DETAIL_KEYS
        if (value := details.get(key)) is not None
    }
    named = {
        key: value
        for key, value in (("connector", connector), ("operation", operation))
        if value
    }
    return _error(
        exc.code or "connector_error", safe_error_text(exc), **named, **carried
    )


def _no_pod() -> dict[str, Any]:
    return _error(
        "no_pod",
        "This conversation is not bound to a pod, so connectors are unavailable.",
    )
