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

from app.modules.web_login.services.scope import BrowserCookie, BrowserOrigin


class WebLoginStatus(StrEnum):
    """Whether the stored session is believed to still work.

    `DEAD` is set when an injection produced a page that still wanted a login.
    It exists so a person is asked to sign in again *before* a run fails on it,
    which is the promise `PS-CONN-022` makes for connector credentials.
    """

    ACTIVE = "ACTIVE"
    DEAD = "DEAD"


@dataclass(frozen=True, slots=True)
class WebLoginSecret:
    """The part that is encrypted at rest and never leaves the backend.

    Nothing here is ever returned to a caller, put in a tool result, or written
    to a log. `WebLogin` deliberately has no field for it: a type that cannot
    carry the secret cannot leak it by accident.
    """

    #: Cookies the site would receive, and local storage for exactly its origin.
    #: Narrowed by `services/scope.py` before it ever reaches this shape.
    cookies: list[BrowserCookie]
    origins: list[BrowserOrigin]

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
    status: WebLoginStatus
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    #: When the stored session is expected to stop working. A hint, not a fact:
    #: sites expire sessions on their own schedule and rarely say so.

    @property
    def is_usable(self) -> bool:
        return self.status is WebLoginStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class PendingSignIn:
    """A sign-in somebody has been asked for and has not answered.

    Read from the paused tool call, not from a row: `origin` and `reason` are
    its arguments, and its being unresolved is what makes it pending. There used
    to be a table saying the same three things, and it drifted from the pause it
    described.
    """

    tool_call_id: str
    origin: str
    reason: str


@dataclass(frozen=True, slots=True)
class SignInOutcome:
    """What came of a person answering.

    Returned to the page so it can say what happened, and carried to the agent
    on the approval's own payload. Not stored: the decision row is the record.
    """

    origin: str
    signed_in: bool
    saved: bool
    saved_detail: str | None = None


__all__ = [
    "PendingSignIn",
    "SignInOutcome",
    "WebLogin",
    "WebLoginSecret",
    "WebLoginStatus",
]
