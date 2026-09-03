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

from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.modules.agent.infrastructure.harnesses.pydantic_ai_retry import (
    HarnessDriverCancelled,
)
from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
)

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
        exc_info=exc,
    )


def user_facing_error_message(exc: Exception) -> str:
    """Return a sanitized, actionable message for the UI.

    Never forward raw provider exception text (which may contain API keys,
    request headers, or model-internal details) into user-visible payloads.
    """
    if isinstance(exc, ModelHTTPError):
        # These three are all "the provider said no", but they need different
        # things from the reader: wait, top up, or fix the config. A single
        # generic message sends people to the wrong place.
        if exc.status_code == 429:
            return (
                "The model provider is rate limiting this workspace (HTTP 429). "
                "The request was retried a few times without success — try "
                "again shortly."
            )
        if exc.status_code == 402:
            return (
                "The model provider rejected the request for billing reasons "
                "(HTTP 402). Please check the provider account's credit or quota."
            )
        if exc.status_code >= 500:
            return (
                f"The model provider is having trouble (HTTP {exc.status_code}) "
                "and the request was retried without success. Nothing you sent "
                "was lost — try again shortly."
            )
        # The remaining 4xx were one message telling everybody to "check the
        # agent runtime configuration", which is the right advice for roughly
        # none of them: a rejected key, a model the provider does not serve and
        # a conversation past the context window need three different actions,
        # and the first is the commonest failure an organization bringing its
        # own key will ever see. The identifiers below are the ones already
        # extracted for the log line, so nothing new is read out of the body.
        _, code = provider_error_identifiers(getattr(exc, "body", None))
        if code == "context_length_exceeded":
            return (
                "This conversation is too long for the model's context window. "
                "Start a new conversation, or switch the agent to a model with "
                "a larger context."
            )
        if exc.status_code in (401, 403):
            return (
                "The model provider rejected the credential for this workspace "
                f"(HTTP {exc.status_code}). Check that the agent runtime's API "
                "key is present, current, and allowed to use this model."
            )
        if exc.status_code == 404:
            # The model is named in the log line and deliberately not here: a
            # provider's model id can carry a private deployment or endpoint
            # name, and this string is written into the transcript.
            return (
                "The model this agent is configured with is not available on "
                "this provider (HTTP 404). Pick another model for the agent, "
                "or check the runtime's base URL."
            )
        return (
            f"The model provider returned an error (HTTP {exc.status_code}). "
            "Please check the agent runtime configuration."
        )
    if is_retryable_stream_error(exc):
        # A transport-level drop that survived every retry. Nothing was lost —
        # each completed message was persisted — so say so, because "check the
        # configuration" sends people hunting a bug that isn't theirs.
        return (
            "The connection to the model provider kept dropping. Nothing you "
            "sent was lost — send another message to pick up where it stopped."
        )
    if isinstance(exc, UnexpectedModelBehavior):
        return (
            "A tool failed repeatedly after several attempts and the run was "
            "stopped. Please check the agent configuration."
        )
    if isinstance(exc, UsageLimitExceeded):
        return (
            "The agent run hit a usage limit. "
            "Please check the agent runtime configuration."
        )
    if isinstance(exc, HarnessDriverCancelled):
        # Whatever the agent was doing stopped part-way, so "try again" is the
        # honest advice. Everything it finished before that point is persisted.
        return (
            "The agent stopped part-way through a step and could not finish. "
            "Nothing you sent was lost — send another message to pick up where "
            "it stopped."
        )
    return (
        "The model provider returned an error. "
        "Please check the agent runtime configuration."
    )


# Per-tool retry budget for the in-process agent. pydantic-ai defaults to 1, which
# turns a single bad/invalid tool call (e.g. arguments that fail schema validation)
# into a fatal run. 5 gives the model several chances to self-correct from the
# validation feedback before the run gives up. Execution errors are handled
# separately by GracefulToolset and never consume this budget.

# Ceiling for the pause between stream-drop retries; see `_retry_backoff`.
