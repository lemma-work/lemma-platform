"""Current authorization context helpers."""

from __future__ import annotations

from contextvars import ContextVar, Token

from opentelemetry import trace

from app.core.authorization.context import Context


_current_context: ContextVar[Context | None] = ContextVar(
    "authorization_current_context",
    default=None,
)


def set_current_context(ctx: Context | None) -> Token:
    _tag_span_with_tenant(ctx)
    return _current_context.set(ctx)


def _tag_span_with_tenant(ctx: Context | None) -> None:
    """Record which tenant the in-flight span is serving.

    Every path that establishes an authorization context comes through here,
    which is also the earliest point the organization is known -- the server
    span opens before authentication has resolved anything, so a span processor
    firing at span start would always be too early.

    Spans only, never a metric label: this turns "the API is slow" into "it is
    slow for this customer" at a cost proportional to sampled traffic, whereas
    the same key on a metric multiplies every series by the customer count.
    """
    # ``getattr`` rather than attribute access: this is a side effect on the
    # authorization path, and callers legitimately pass context-shaped objects
    # that are not ``Context``. Tagging a span is never worth failing a
    # request over.
    organization_id = getattr(ctx, "organization_id", None)
    if organization_id is None:
        return
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("lemma.organization_id", str(organization_id))


def get_current_context() -> Context | None:
    return _current_context.get()


def reset_current_context(token: Token) -> None:
    _current_context.reset(token)
