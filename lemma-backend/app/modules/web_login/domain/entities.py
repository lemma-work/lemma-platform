"""What a saved web login is, and what a request to create one is.

One person's own way back in to a site Lemma has no connector for. The same idea
as a connector account with a different mechanism, which is why the authorization
model copies `CONNECTOR_ACCOUNT` rather than inventing a parallel one.

**Only a session is ever kept.** The cookies and local storage a browser holds
after somebody has signed in: the same class of secret Lemma already keeps for
connectors, usually weaker, and revocable by the person simply logging out at
the site. Never a password. `connectors-and-accounts.md` promises the system
"shall never ask them for their provider password", and a stored password is a
different and worse class of secret -- reused across sites, not revocable
without changing it everywhere. An earlier draft of this feature carried a
password field "for unattended runs"; the answer to that case is a longer-lived
session or a real connector, not a vault nobody promised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class WebLoginStatus(StrEnum):
    """Whether the stored session is believed to still work.

    `DEAD` is set when an injection produced a page that still wanted a login.
    It exists so a person is asked to sign in again *before* a run fails on it,
    which is the promise `PS-CONN-022` makes for connector credentials.
    """

    ACTIVE = "ACTIVE"
    DEAD = "DEAD"


class SignInRequestStatus(StrEnum):
    PENDING = "PENDING"
    SIGNED_IN = "SIGNED_IN"
    DECLINED = "DECLINED"


@dataclass(frozen=True, slots=True)
class WebLoginSecret:
    """The part that is encrypted at rest and never leaves the backend.

    Nothing here is ever returned to a caller, put in a tool result, or written
    to a log. `WebLogin` deliberately has no field for it: a type that cannot
    carry the secret cannot leak it by accident.
    """

    #: Cookies the site would receive, and local storage for exactly its origin.
    #: Narrowed by `services/scope.py` before it ever reaches this shape.
    cookies: list[dict]
    origins: list[dict]

    def is_empty(self) -> bool:
        return not self.cookies and not self.origins


@dataclass(frozen=True, slots=True)
class WebLogin:
    """A saved login, without its secret.

    This is the shape that gets listed, returned from the API and logged.
    """

    id: UUID
    user_id: UUID
    origin: str
    label: str
    status: WebLoginStatus
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    #: When the stored session is expected to stop working. A hint, not a fact:
    #: sites expire sessions on their own schedule and rarely say so.
    expires_hint_at: datetime | None = None

    @property
    def is_usable(self) -> bool:
        return self.status is WebLoginStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class SignInRequest:
    """An agent waiting for a person to sign in to a site.

    Durable, in Postgres, rather than a Redis key with a fifteen-minute life.
    The run it belongs to is paused indefinitely -- `PS-AGENT-020` promises a
    pause waits rather than times out -- and a request that expired while the
    conversation was still waiting would leave the person with a dead link and
    the agent with nothing to resume from. The *browser* is the ephemeral part,
    and it is re-opened when somebody arrives.
    """

    id: UUID
    user_id: UUID
    origin: str
    reason: str
    status: SignInRequestStatus
    created_at: datetime
    conversation_id: UUID | None = None
    #: The paused tool call this resolves. Also the approval id, which is what
    #: lets an existing approvals endpoint resume the run.
    tool_call_id: str | None = None
    resolved_at: datetime | None = None
    #: Whether a session was captured when the person said they were done, and
    #: the sentence to show them if it was not.
    saved: bool = False
    saved_detail: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status is SignInRequestStatus.PENDING


__all__ = [
    "SignInRequest",
    "SignInRequestStatus",
    "WebLogin",
    "WebLoginSecret",
    "WebLoginStatus",
]
