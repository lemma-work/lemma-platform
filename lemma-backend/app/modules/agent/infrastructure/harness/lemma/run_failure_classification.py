"""Why a run failed, in one sentence its reader can act on.

There used to be two classifiers. The harness turned an exception into the
error event it emits; the runner turned an exception that escaped the harness
into the error it records. They were written apart and drifted: the same
exception read "the model provider returned an error" in one place and "the run
failed" in the other, and only one of them knew some failures existed at all.
`classify_run_failure` is the one answer both read.

Raw provider text is never forwarded. It can echo the request -- keys and
headers included -- so every sentence here is ours, chosen from the exception's
type and the short enum-like identifiers a provider puts beside its prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transient_errors import is_transient_database_error
from app.modules.agent.infrastructure.harness.lemma.retry import (
    HarnessDriverCancelled,
)
from app.modules.agent.infrastructure.transport_errors import (
    connection_failure_message,
    is_retryable_stream_error,
    response_budget_expiry,
    response_budget_message,
)
from app.modules.usage.contracts import UsageLimitExceededError

__all__ = [
    "PROVIDER_HTTP_ERROR",
    "TRANSIENT_DATABASE_ERROR",
    "RunFailure",
    "classify_run_failure",
    "is_usage_limit_error",
    "provider_error_identifiers",
    "run_failure_reason",
]

# Provider error codes are short snake/kebab identifiers. Anything else is prose
# (or worse) and is not read.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")

#: pydantic-ai's wording when a response stopped at the output cap with nothing
#: usable in it -- typically a reasoning model that spent the whole cap thinking.
_TOKEN_LIMIT = re.compile(r"\btoken limit\b.*\bexceeded\b", re.IGNORECASE)

_SETTINGS = "Settings → Models"

#: A deadlock, a serialization failure or an exhausted connection pool: Lemma's
#: own database was momentarily contended. Nothing about the teammate, its model
#: or the message is wrong, and the same turn sent again is expected to work.
TRANSIENT_DATABASE_ERROR = "TRANSIENT_DATABASE_ERROR"

#: The model provider answered with an HTTP error -- a rejected key, a model it
#: does not serve, a base URL that is not its API. The status is the run's
#: failure reason (``http_<status>``); the sentence says what to do about it.
PROVIDER_HTTP_ERROR = "PROVIDER_HTTP_ERROR"

_DATABASE_BUSY = (
    "Lemma could not run this turn because its database was briefly busy. "
    "That has nothing to do with this teammate or its settings. "
    "Nothing you sent was lost — send your message again."
)

_UNCLASSIFIED = (
    f"The run failed. Check the model settings for this teammate ({_SETTINGS})."
)


@dataclass(frozen=True, slots=True)
class RunFailure:
    """A failure as its reader sees it: an optional code and one sentence."""

    code: str | None
    message: str


def _identifier(value: object) -> str | None:
    if isinstance(value, str) and _IDENTIFIER.match(value):
        return value
    return None


def provider_error_identifiers(body: object) -> tuple[str | None, str | None]:
    """Return ``(kind, code)`` from a provider error body, when present.

    Tolerant of both shapes providers use -- OpenAI-compatible
    ``{"error": {"type", "code"}}`` and Anthropic's top-level ``type`` -- and of
    a bare ``{"type", "code"}``, because gateways vary. Returns ``(None, None)``
    rather than guessing when the body is an unrecognised shape.
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


def is_usage_limit_error(exc: BaseException) -> bool:
    """Whether this run died because the workspace is out of allowance."""
    if isinstance(exc, BaseExceptionGroup):
        return any(is_usage_limit_error(child) for child in exc.exceptions)
    return isinstance(exc, UsageLimitExceededError) and exc.reason == "exhausted"


def run_failure_reason(exc: BaseException) -> str | None:
    if isinstance(exc, BaseExceptionGroup):
        if is_usage_limit_error(exc):
            return "exhausted"
        return next(
            (
                reason
                for child in exc.exceptions
                if (reason := run_failure_reason(child))
            ),
            None,
        )
    if isinstance(exc, ModelHTTPError):
        return f"http_{exc.status_code}"
    return exc.reason if isinstance(exc, UsageLimitExceededError) else None


def classify_run_failure(exc: BaseException) -> RunFailure:
    """The code and sentence for a run that ended on ``exc``."""
    if isinstance(exc, BaseExceptionGroup):
        return _classify_group(exc)
    if is_usage_limit_error(exc):
        return RunFailure(
            "USAGE_LIMIT_EXCEEDED",
            "This run cannot continue because the workspace has used its "
            "available usage allowance. Usage resets with the billing period, "
            "or the plan limit can be raised.",
        )
    if isinstance(exc, DomainError):
        if exc.code == "MODEL_PROVIDER_ATTEMPTS_EXHAUSTED" and isinstance(
            exc.__cause__, ModelHTTPError
        ):
            return RunFailure(exc.code, _http_error_message(exc.__cause__))
        # A DomainError's message is already the sentence we would want: it was
        # authored to be read by the person whose run just failed.
        return RunFailure(exc.code, exc.message)
    if isinstance(exc, ModelHTTPError):
        return RunFailure(PROVIDER_HTTP_ERROR, _http_error_message(exc))
    if is_transient_database_error(exc):
        return RunFailure(TRANSIENT_DATABASE_ERROR, _DATABASE_BUSY)
    return RunFailure(None, _runtime_failure_message(exc))


def _classify_group(exc: BaseExceptionGroup[BaseException]) -> RunFailure:
    if is_usage_limit_error(exc):
        return classify_run_failure(UsageLimitExceededError())
    domain_error = next(
        (child for child in exc.exceptions if isinstance(child, DomainError)), None
    )
    chosen = classify_run_failure(domain_error or exc.exceptions[0])
    code = chosen.code or next(
        (
            code
            for child in exc.exceptions
            if (code := classify_run_failure(child).code)
        ),
        None,
    )
    return RunFailure(code, chosen.message)


def _runtime_failure_message(exc: BaseException) -> str:
    """The sentence for a failure that is not the provider refusing a request."""
    expiry = response_budget_expiry(exc)
    if expiry is not None:
        return response_budget_message(expiry)
    if _ran_out_of_output_room(exc):
        return (
            "The model ran out of room to answer: it reached its output limit "
            "before it finished. Nothing you sent was lost — ask for a "
            "shorter answer or a smaller step, or raise this model's output "
            f"limit ({_SETTINGS})."
        )
    if is_retryable_stream_error(exc):
        # A transport-level drop that survived every retry. No mention of the
        # Retry button: a run that had already been talking is refused it.
        return connection_failure_message(exc)
    if isinstance(exc, UnexpectedModelBehavior):
        return (
            "A tool failed repeatedly after several attempts and the run was "
            "stopped. Please check the agent configuration."
        )
    if isinstance(exc, UsageLimitExceeded):
        return f"The run hit a usage limit. Check the model settings for this teammate ({_SETTINGS})."
    if isinstance(exc, HarnessDriverCancelled):
        # Whatever the agent was doing stopped part-way, so "try again" is the
        # honest advice. Everything it finished before that point is persisted.
        return (
            "The agent stopped part-way through a step and could not finish. "
            "Nothing you sent was lost — send another message to pick up "
            "where it stopped."
        )
    if not isinstance(exc, Exception):
        return "Agent run was interrupted (timeout or shutdown)"
    return _UNCLASSIFIED


def _ran_out_of_output_room(exc: BaseException) -> bool:
    """A response cut off by the output cap, which no tool had any part in.

    Reported as a tool failing repeatedly, these sent operators hunting through
    agent configuration for a fault that was the output budget all along.
    """
    if isinstance(exc, IncompleteToolCall):
        return True
    return isinstance(exc, UnexpectedModelBehavior) and bool(
        _TOKEN_LIMIT.search(exc.message)
    )


def _http_error_message(exc: ModelHTTPError) -> str:
    """The provider said no; which of these it was decides what the reader does."""
    status = exc.status_code
    if status == 429:
        return (
            "The model provider is rate limiting this workspace (HTTP 429). "
            "The request was retried a few times without success — try "
            "again shortly."
        )
    if status == 402:
        return (
            "The model provider rejected the request for billing reasons "
            "(HTTP 402). Please check the provider account's credit or quota."
        )
    if status >= 500:
        return (
            f"The model provider is having trouble (HTTP {status}) and the "
            "request was retried without success. Nothing you sent was lost "
            "— try again shortly."
        )
    # A rejected key, a model the provider does not serve and a conversation
    # past the context window need three different actions, so the remaining
    # 4xx are told apart by the identifiers already extracted for the log line.
    _, code = provider_error_identifiers(getattr(exc, "body", None))
    if code == "context_length_exceeded":
        return (
            "This conversation is too long for the model's context window. "
            "Start a new conversation, or switch the agent to a model with a "
            "larger context."
        )
    if status in (401, 403):
        return (
            f"The model provider rejected this model's API key (HTTP {status}). "
            f"Check the key in {_SETTINGS}: that it is current and allowed to use "
            "this model."
        )
    if status == 404:
        # The model is named in the log line and deliberately not here: a
        # provider's model id can carry a private deployment or endpoint name,
        # and this string is written into the transcript.
        return (
            "The model this teammate is set to use is not available on this "
            "provider (HTTP 404). Pick another model for the teammate, or check "
            f"the provider's API base URL in {_SETTINGS}."
        )
    return (
        f"The model provider returned an error (HTTP {status}). Check the model "
        f"settings for this teammate ({_SETTINGS})."
    )
