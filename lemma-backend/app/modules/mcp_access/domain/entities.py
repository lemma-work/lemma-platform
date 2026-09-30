"""What an outside MCP client holds when a person connects it to a pod.

A person adds a pod to Claude, ChatGPT or another MCP client by URL. The client
registers itself (or names a metadata document it hosts), sends the person
here to sign in and consent, and gets back tokens that work on that one pod's
MCP endpoint and nowhere else. `docs/architecture/mcp-connector.md` has the
whole exchange and the reasons for each choice.

Three things persist, and they are the whole model:

* a **client** -- who is asking. Registered dynamically (RFC 7591) or resolved
  from a client ID metadata document, and never trusted for more than its
  redirect URIs;
* a **grant** -- that this person let that client use that pod, with these
  scopes. It is the row the person sees under connected apps and revokes;
* **tokens** -- stored only as hashes, each belonging to one grant, so revoking
  the grant ends every token at once, with no expiry to wait out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Scope(StrEnum):
    """What a grant lets a client do in its pod.

    Two, because the tools split cleanly in two: those that only read and those
    that write. A finer set would ask a person to reason about tools they have
    never seen, on a consent screen they will read once.
    """

    READ = "pod:read"
    WRITE = "pod:write"


ALL_SCOPES: tuple[Scope, ...] = (Scope.READ, Scope.WRITE)


def parse_scopes(raw: list[str] | None) -> frozenset[Scope]:
    """The scopes named, and no others -- an empty list is no scopes.

    "Asked for nothing, so offer everything" is decided once, at the authorize
    endpoint, where the request arrives. Read anywhere later, an empty list is
    a grant or token that holds nothing, and treating it as everything is how
    a read-only answer to a write-only request came back as read and write.

    Unknown values are dropped rather than refused: the authorize endpoint has
    already validated the request against `ALL_SCOPES`, so anything else here is
    a stored value from a newer build, and ignoring it grants less, never more.
    """
    if not raw:
        return frozenset()
    known = {scope.value: scope for scope in ALL_SCOPES}
    return frozenset(known[value] for value in raw if value in known)


class ClientRegistration(StrEnum):
    DYNAMIC = "dynamic"
    """RFC 7591: the client POSTed its metadata and was issued an id."""

    METADATA_DOCUMENT = "metadata_document"
    """The client id is an HTTPS URL serving the client's own metadata."""


class TokenKind(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class ConnectedApp:
    """One grant, as the person who made it sees it."""

    grant_id: UUID
    user_id: UUID
    pod_id: UUID
    client_id: str
    client_name: str
    client_uri: str | None
    scopes: frozenset[Scope]
    created_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class McpPrincipal:
    """Who is calling a pod's MCP endpoint with an access token, and what for.

    Everything the tool layer needs and nothing it should not have: the person
    the client acts for, the pod the token is bound to, and the scopes the
    person agreed to. The client's name rides along for the audit trail.
    """

    user_id: UUID
    pod_id: UUID
    grant_id: UUID
    client_id: str
    client_name: str
    scopes: frozenset[Scope]

    def allows(self, scope: Scope) -> bool:
        return scope in self.scopes
