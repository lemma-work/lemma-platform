"""Server-side diagnostics for provider HTTP failures.

Production logged only the status code, which made these undiagnosable: a week
of 400s could be a context overflow, a malformed tool schema, or an image sent
to a text-only model, and nothing distinguished them.

The obvious fix — log the response body — is forbidden here, and rightly: the
logging contract (`app/core/log/log.py::_PROHIBITED_FIELDS`) bans `body`,
`message`, `headers` and friends because provider errors echo the request, keys
included. So instead of the body we extract the two short, enum-like identifiers
every provider puts beside the prose:

    OpenAI-compatible: {"error": {"type": "invalid_request_error",
                                  "code": "context_length_exceeded", ...}}
    Anthropic:         {"type": "error", "error": {"type": "invalid_request_error"}}

Those are the fields that actually separate one 400 from another, and they carry
no user or credential data. Anything that isn't a short identifier is dropped
rather than truncated — a value long enough to be prose is prose.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Provider error codes are short snake/kebab identifiers. Anything else is prose
# (or worse) and is not logged.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")


def _identifier(value: Any) -> str | None:
    if isinstance(value, str) and _IDENTIFIER.match(value):
        return value
    return None


def provider_error_identifiers(body: Any) -> tuple[str | None, str | None]:
    """Return ``(kind, code)`` from a provider error body, when present.

    Tolerant of both shapes above and of a bare ``{"type": ..., "code": ...}``,
    because OpenAI-compatible gateways vary. Returns ``(None, None)`` rather than
    guessing when the body is an unrecognised shape.
    """
    if not isinstance(body, dict):
        return None, None
    inner = body.get("error")
    source = inner if isinstance(inner, dict) else body
    kind = _identifier(source.get("type"))
    code = _identifier(source.get("code"))
    if kind is None and isinstance(inner, dict):
        # Anthropic puts the envelope type at the top level.
        kind = _identifier(body.get("type"))
    return kind, code


def log_model_http_error(
    exc: ModelHTTPError,
    *,
    agent_run_id: object,
    model_name: str | None = None,
) -> None:
    """Record everything the provider told us about why the request failed.

    The `provider_error_*` fields are the enum-like identifiers that make these
    groupable (`context_length_exceeded` vs `invalid_request_error`); the
    exception message and traceback come from `exc_info` and carry the prose the
    provider actually returned.
    """
    kind, code = provider_error_identifiers(getattr(exc, "body", None))
    logger.error(
        "agent.pydantic_ai.model_request_status_model.failed",
        status_code=exc.status_code,
        model_name=model_name or getattr(exc, "model_name", None),
        agent_run_id=str(agent_run_id),
        provider_error_kind=kind,
        provider_error_code=code,
        exc_info=True,
    )
