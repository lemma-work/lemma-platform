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

from uuid import UUID

from pydantic_ai.exceptions import ModelHTTPError

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harness.lemma.run_failure_classification import (
    classify_run_failure,
    provider_error_identifiers as provider_error_identifiers,
    run_failure_reason,
)
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType

logger = get_logger(__name__)


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


def failure_event(exc: BaseException, *, agent_run_id: UUID) -> AgentEvent:
    """The ERROR a run ends on: the sentence, and the code a client keys off.

    The code used to stay behind in the harness, so a run that ended here was
    recorded with its message and no code -- a provider refusal read as
    uncategorised to anything that matched on it.
    """
    failure = classify_run_failure(exc)
    return AgentEvent(
        type=AgentEventType.ERROR,
        data=failure.message,
        agent_run_id=agent_run_id,
        error_code=failure.code,
        error_reason=run_failure_reason(exc),
    )
