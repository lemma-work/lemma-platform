"""What a saved web login is.

One person's own way back in to a site Lemma has no connector for. The same idea
as a connector account, with a different mechanism — which is why the UI shelves
the two together and the authorization model copies `CONNECTOR_ACCOUNT` rather
than inventing a parallel one.

Two kinds, and the order matters:

``SESSION`` is the primary one — the cookies and local storage a browser holds
after somebody has signed in. It is the same class of secret Lemma already keeps
for connectors (an OAuth refresh token), usually weaker, and always revocable by
the person logging out.

``CREDENTIAL`` is a password, and it is a new class: reused across sites, and
not revocable without changing it. It exists for the one case a session cannot
serve — an unattended run at 3am, where nobody is awake to be asked — and is
opt-in per site for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class WebLoginKind(StrEnum):
    SESSION = "SESSION"
    CREDENTIAL = "CREDENTIAL"


@dataclass(frozen=True, slots=True)
class WebLoginSecret:
    """The part that is encrypted at rest and never leaves the backend.

    Nothing here is ever returned to a caller, put in a tool result, or written
    to a log. The bridge that uses it returns ``None`` for exactly that reason.
    """

    #: `agent-browser state save` output: cookies plus local storage.
    state: str | None = None
    username: str | None = None
    password: str | None = None
    #: Base32 TOTP seed. The *seed* never enters the sandbox — the backend
    #: generates the six digits and injects those.
    totp_seed: str | None = None


@dataclass(frozen=True, slots=True)
class WebLogin:
    """A saved login, without its secret.

    The secret is deliberately not a field: this is the shape that gets listed,
    returned from the API and logged, and a type that cannot carry the secret
    cannot leak it by accident.
    """

    id: UUID
    user_id: UUID
    origin: str
    label: str
    kind: WebLoginKind
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    #: When the stored session is expected to stop working. A hint, not a fact:
    #: sites expire sessions on their own schedule and rarely say so.
    expires_hint_at: datetime | None = None

    @property
    def has_password(self) -> bool:
        return self.kind is WebLoginKind.CREDENTIAL
