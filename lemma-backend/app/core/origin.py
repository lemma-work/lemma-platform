"""How work entered the platform, as one canonical noun.

``ActorType`` (``app.core.authorization.context``) says *who* acted. This says
*how the work arrived*, which is a genuinely independent question: people chat
through the web UI and through surfaces, agents usually work through the SDK —
but people use the SDK too, and agents post to surfaces. Collapsing the two
into one field makes "how much of this pod's work is human?" unanswerable.

The platform already used the word *origin* for this in two narrower places —
``Conversation.origin_type`` (a loose string) and ``NotificationOriginKind``
(an enum covering notifications only). This is the canonical spine both should
converge on. It is deliberately *not* called "surface": a surface is a specific
product noun — a chat platform where an agent answers pod members — and it is
one origin among many.

Origin is resolved once at the edge and carried in a contextvar, the same way
:mod:`app.core.request_context` carries request and correlation ids. Work that
no request started — schedules, table triggers, outbox consumers — sets its
origin where it is enqueued rather than leaving it to be guessed downstream.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Iterator
import re


class OriginKind(str, Enum):
    """The full width of ways work reaches a pod."""

    # People, directly.
    WEB = "WEB"
    """The Next app in a browser."""

    DESKTOP = "DESKTOP"
    """The Next app in the Tauri webview."""

    CLI = "CLI"
    """``lemma`` commands. Typed by a person or run by a coding agent — which
    one is the actor's question, not origin's."""

    APP = "APP"
    """A published pod app. Members only, per REACH_RULE."""

    SURFACE = "SURFACE"
    """Slack, Teams, WhatsApp, Telegram, Gmail, Outlook. Members only, per
    REACH_RULE. The platform rides in ``Origin.platform``."""

    # Programmatic.
    SDK = "SDK"
    """lemma-python or lemma-typescript against the API."""

    MCP_POD = "MCP_POD"
    """The pod MCP route (``/agent-runtime/pods``)."""

    MCP_CONVERSATION = "MCP_CONVERSATION"
    """The conversation MCP route (``/agent-runtime/conversations``)."""

    AGENT_HOST = "AGENT_HOST"
    """A user-owned coding agent over ACP. The agent family (claude-code,
    codex, cursor, opencode) rides in ``Origin.platform`` — worth knowing
    precisely, because bring-your-own-agent is the pitch."""

    WORKSPACE = "WORKSPACE"
    """Code running inside a sandbox."""

    # Nobody present.
    SCHEDULE = "SCHEDULE"
    """A cron trigger firing."""

    DATA_TRIGGER = "DATA_TRIGGER"
    """A table write matching a trigger condition."""

    WORKFLOW = "WORKFLOW"
    """A workflow node doing its work."""

    CONNECTOR = "CONNECTOR"
    """Work read from outside the pod. Per REACH_RULE this is the *only* path
    by which a non-member reaches a pod, which is why connector-driven work
    must never be summed with surface and app work into one "engagement"
    number: they measure different products."""

    IMPORT = "IMPORT"
    """A pod bundle being imported or remixed. Separated from SDK/CLI so the
    share -> import -> remix loop stays measurable however it was driven."""

    SYSTEM = "SYSTEM"
    """Platform-internal work with no external trigger."""


#: Origins that carry a meaningful ``platform``, and the exact values allowed.
#: Default-deny: a platform not listed here is dropped rather than forwarded,
#: so a new integration cannot widen the analytics dimension by accident.
ORIGIN_PLATFORMS: dict[OriginKind, frozenset[str]] = {
    OriginKind.SURFACE: frozenset(
        {"slack", "teams", "whatsapp", "telegram", "gmail", "outlook", "resend"}
    ),
    OriginKind.CONNECTOR: frozenset({"native", "composio", "mcp"}),
    OriginKind.AGENT_HOST: frozenset({"claude-code", "codex", "cursor", "opencode"}),
}


@dataclass(frozen=True, slots=True)
class Origin:
    kind: OriginKind
    platform: str | None = None

    def __post_init__(self) -> None:
        allowed = ORIGIN_PLATFORMS.get(self.kind)
        if self.platform is None:
            return
        if allowed is None or self.platform not in allowed:
            # Frozen dataclass: drop the unknown value rather than raise. A
            # mislabelled origin should degrade the dimension, never fail the
            # request that carried it.
            object.__setattr__(self, "platform", None)

    def as_properties(self) -> dict[str, str]:
        values = {"origin": self.kind.value}
        if self.platform is not None:
            values["origin_platform"] = self.platform
        return values


_origin: ContextVar[Origin | None] = ContextVar("lemma_origin", default=None)


def current_origin() -> Origin | None:
    return _origin.get()


@contextmanager
def origin_scope(origin: Origin | None) -> Iterator[None]:
    """Bind ``origin`` for the duration of the block."""
    token = _origin.set(origin)
    try:
        yield
    finally:
        _origin.reset(token)


# ``X-Lemma-Client`` is already sent by every SDK request as
# ``<client>/<version>`` (lemma-python/lemma_sdk/transport.py). Clients gain an
# origin token by naming themselves precisely; the mapping stays here so an
# unrecognised client degrades to SDK rather than inventing a dimension value.
_CLIENT_HEADER_RE = re.compile(r"^([A-Za-z0-9._-]{1,64})/([A-Za-z0-9._+-]{1,32})$")

_CLIENT_ORIGINS: dict[str, OriginKind] = {
    "lemma-web": OriginKind.WEB,
    "lemma-desktop": OriginKind.DESKTOP,
    "lemma-cli": OriginKind.CLI,
    "lemma-app": OriginKind.APP,
    "lemma-sdk-py": OriginKind.SDK,
    "lemma-sdk-ts": OriginKind.SDK,
}


#: Routes whose path *is* the origin. An MCP client sends no Lemma client
#: header -- it is somebody else's agent runtime -- so the mount point is the
#: only honest signal, and it is a better one than a header a caller controls.
_PATH_ORIGINS: tuple[tuple[str, OriginKind], ...] = (
    ("/agent-runtime/pods", OriginKind.MCP_POD),
    ("/agent-runtime/conversations", OriginKind.MCP_CONVERSATION),
)


def origin_for_path(path: str) -> Origin | None:
    for prefix, kind in _PATH_ORIGINS:
        if path.startswith(prefix):
            return Origin(kind)
    return None


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """What the caller said it is. ``version`` is kept separate from origin so
    a version string can never widen the origin dimension."""

    origin: Origin
    client: str | None = None
    version: str | None = None


def resolve_client_identity(header_value: str | None) -> ClientIdentity:
    """Parse ``X-Lemma-Client`` into an origin plus a bounded client/version.

    An absent, malformed, or unrecognised header resolves to ``SDK``: something
    called the API programmatically and did not identify itself, which is what
    SDK means here. It never resolves to ``WEB`` — an unknown caller must not
    be counted as a person in a browser.
    """
    if not header_value:
        return ClientIdentity(Origin(OriginKind.SDK))
    match = _CLIENT_HEADER_RE.match(header_value.strip())
    if match is None:
        return ClientIdentity(Origin(OriginKind.SDK))
    client, version = match.group(1), match.group(2)
    kind = _CLIENT_ORIGINS.get(client)
    if kind is None:
        return ClientIdentity(Origin(OriginKind.SDK), client=None, version=None)
    return ClientIdentity(Origin(kind), client=client, version=version)
