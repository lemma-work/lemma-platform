"""The access token a web visitor's requests carry, and who it says they are.

Minted when a session starts or is refreshed (``POST /public/web/{key}/session``)
and sent as ``Authorization: Bearer`` on everything else. It lives fifteen
minutes and is never stored by the page, which keeps only the session secret:
a token read out of a page is worth a quarter of an hour, and the session it
names can be revoked underneath it (``PublicVisitorDep`` checks).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.crypto.tokens import (
    InvalidSignedToken,
    mint_signed_token,
    verify_signed_token,
)
from app.modules.contacts.contracts.visitor_sessions import (
    VisitorSession,
    VisitorStrength,
)

PURPOSE = "visitor-access"
ACCESS_TOKEN_SECONDS = 900


@dataclass(frozen=True, slots=True)
class VisitorAccess:
    """What a valid access token says: the session, and who it is."""

    session_id: UUID
    pod_id: UUID
    widget_id: UUID
    organization_id: UUID | None
    contact_id: UUID | None
    strength: VisitorStrength

    @property
    def actor_id(self) -> str:
        """Who acts, for the audit trail: the contact, or this session."""
        if self.contact_id is not None:
            return f"contact:{self.contact_id}"
        return f"visitor:{self.session_id}"


@dataclass(frozen=True, slots=True)
class Visitor:
    """A visitor's request: their session as the token names it, and the
    authorization context built from it."""

    session: VisitorAccess
    context: Context


def mint_visitor_access(
    session: VisitorSession, *, organization_id: UUID | None
) -> str:
    claims: dict[str, object] = {
        "sid": str(session.id),
        "pod": str(session.pod_id),
        "wid": str(session.widget_id),
        "str": session.strength.value,
    }
    if organization_id is not None:
        claims["org"] = str(organization_id)
    if session.contact_id is not None:
        claims["cid"] = str(session.contact_id)
    return mint_signed_token(PURPOSE, claims, ttl_seconds=ACCESS_TOKEN_SECONDS)


def _uuid(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))


def read_visitor_access(token: str) -> VisitorAccess:
    """The access a token grants, or ``InvalidSignedToken``."""
    claims = verify_signed_token(PURPOSE, token)
    try:
        return VisitorAccess(
            session_id=UUID(str(claims["sid"])),
            pod_id=UUID(str(claims["pod"])),
            widget_id=UUID(str(claims["wid"])),
            organization_id=_uuid(claims.get("org")),
            contact_id=_uuid(claims.get("cid")),
            strength=VisitorStrength(str(claims["str"])),
        )
    except (KeyError, ValueError) as exc:
        raise InvalidSignedToken("malformed visitor claims") from exc
