"""Always trace the requests of chosen accounts, whatever the sample ratio.

``OTEL_ALWAYS_SAMPLE_USER_IDS`` names user ids (comma-separated). A request
whose access token -- the ``sAccessToken`` cookie or a bearer token -- names
one of them as its ``sub`` is sampled, and everything it causes downstream
follows, because the sampler is parent-based and outbox events carry the
sampled ``traceparent``.

The decision is made when the server span starts, which is before
authentication, so the token's ``sub`` is read WITHOUT verifying it. That is
safe for exactly one reason: the only thing it can decide is whether this
request's spans are kept. A forged token can make a request get traced -- as a
forged ``traceparent`` with the sampled flag always could -- and authentication
still rejects it afterwards. Nothing here is read by authorization.

The check is a base64 decode of the token's middle segment, only when the
allowlist is non-empty: no database, no network, no signature.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from contextvars import ContextVar
import json
import types

from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace import Link, SpanKind, get_current_span
from opentelemetry.util.types import Attributes
from starlette.types import ASGIApp, Receive, Scope, Send

_forced: ContextVar[bool] = ContextVar("lemma_force_sample", default=False)
_MAX_TOKEN = 8192


def parse_user_ids(raw: str | None) -> frozenset[str]:
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def _header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1", "replace")
    return ""


def _token(headers: Sequence[tuple[bytes, bytes]]) -> str:
    authorization = _header(headers, b"authorization")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    for part in _header(headers, b"cookie").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "sAccessToken":
            return value.strip()
    return ""


def unverified_subject(headers: Sequence[tuple[bytes, bytes]]) -> str | None:
    """The ``sub`` the presented access token claims. NOT verified: see above."""
    token = _token(headers)
    if not token or len(token) > _MAX_TOKEN:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    return subject if isinstance(subject, str) else None


class ForceSampleMiddleware:
    """Marks a request from an allowlisted account before its span starts."""

    def __init__(self, app: ASGIApp, user_ids: frozenset[str]) -> None:
        self.app = app
        self.user_ids = user_ids

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or (
            unverified_subject(scope.get("headers") or ()) not in self.user_ids
        ):
            await self.app(scope, receive, send)
            return
        token = _forced.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            _forced.reset(token)


class ForcibleSampler(Sampler):
    """``delegate``, except that a marked request is always sampled."""

    def __init__(self, delegate: Sampler) -> None:
        self.delegate = delegate

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state=None,  # noqa: ANN001 - the SDK's own signature
    ) -> SamplingResult:
        if _forced.get():
            # The SDK does not pass ``trace_state``; keep the parent's, as the
            # standard samplers do, so a vendor's ``tracestate`` survives.
            parent = get_current_span(parent_context).get_span_context()
            return SamplingResult(
                Decision.RECORD_AND_SAMPLE,
                attributes,
                trace_state if trace_state is not None else parent.trace_state,
            )
        return self.delegate.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"ForcibleSampler{{{self.delegate.get_description()}}}"


def wrap_outermost(app: object, raw_user_ids: str | None) -> None:
    """Put the marker outside the tracing middleware, so the mark is set
    before the server span -- and its sampling decision -- starts. Nothing is
    installed when no account is named."""
    user_ids = parse_user_ids(raw_user_ids)
    if not user_ids:
        return
    build: Callable[[], ASGIApp] = app.build_middleware_stack  # type: ignore[attr-defined]

    def build_middleware_stack(self: object) -> ASGIApp:
        return ForceSampleMiddleware(build(), user_ids)

    app.build_middleware_stack = types.MethodType(build_middleware_stack, app)  # type: ignore[attr-defined]


__all__ = [
    "ForceSampleMiddleware",
    "ForcibleSampler",
    "parse_user_ids",
    "unverified_subject",
    "wrap_outermost",
]
