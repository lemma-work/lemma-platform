"""Who a pod-database session reads as, in the terms its row policies read.

Two policies exist. Per-user isolation (``enable_rls``) lets a row through to
its ``user_id`` or to a pod admin. The contact policy (contact-owned tables)
lets a row through to a member, or to the contact its ``contact_id`` names.
Both read transaction-local settings, and both fail closed: a session that set
nothing sees nothing.

So every session that runs under a policy names its principal in full -- the
four settings below, always all four -- and the principal comes from the
authorization ``Context``, never from a loose ``user_id``. That is what lets a
contact's run, an anonymous visitor's run and a member's run read the same
table and each get exactly their own share of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.core.authorization.context import Context

#: The column a contact-owned table names its contact in. Stamped, never asked.
CONTACT_COLUMN = "contact_id"

#: The member a session reads as when it reads as no member. No row is stamped
#: with it, so per-user isolation matches nothing -- where an empty setting
#: would fail the policy's uuid cast and turn an empty answer into an error.
NOBODY = UUID(int=0)

USER_SETTING = "app.current_user_id"
ADMIN_SETTING = "app.current_user_is_pod_admin"
AUDIENCE_SETTING = "app.rls_audience"
CONTACT_SETTING = "app.current_contact_id"


class RowAudience(StrEnum):
    """Which side of the pod a session reads from."""

    #: Somebody in the pod: every contact's rows, and their own per-user rows.
    MEMBER = "member"
    #: A contact: only rows naming them, and no member's rows.
    CONTACT = "contact"
    #: Nobody in particular: no contact's rows and no member's rows.
    OUTSIDER = "outsider"


@dataclass(frozen=True, slots=True)
class RowPrincipal:
    """The four settings one session's row policies are evaluated against."""

    audience: RowAudience
    user_id: UUID | None = None
    contact_id: UUID | None = None
    is_pod_admin: bool = False

    @classmethod
    def of(cls, ctx: "Context", *, is_pod_admin: bool = False) -> "RowPrincipal":
        """The principal an authorization context reads as.

        A context naming a contact reads as that contact whatever its actor
        type -- a contact's function run is a workload with grants, and the
        contact on it is what keeps its reads to that contact's rows.
        """
        if ctx.contact_id is not None:
            return cls.contact(ctx.contact_id)
        if ctx.is_outsider:
            return cls(audience=RowAudience.OUTSIDER)
        return cls(
            audience=RowAudience.MEMBER,
            user_id=ctx.user_id,
            is_pod_admin=is_pod_admin,
        )

    @classmethod
    def for_user(
        cls, user_id: UUID | None, *, is_pod_admin: bool = False
    ) -> "RowPrincipal":
        """The principal of a record write or read that carries only a user.

        No user is nobody: an outsider, who matches no per-user row.
        """
        if user_id is None:
            return cls(audience=RowAudience.OUTSIDER)
        return cls(
            audience=RowAudience.MEMBER, user_id=user_id, is_pod_admin=is_pod_admin
        )

    @classmethod
    def contact(cls, contact_id: UUID) -> "RowPrincipal":
        return cls(audience=RowAudience.CONTACT, contact_id=contact_id)

    def settings(self) -> dict[str, str]:
        """The setting values, exactly as written and as read back."""
        return {
            USER_SETTING: str(self.user_id or NOBODY),
            ADMIN_SETTING: "true" if self.is_pod_admin else "false",
            AUDIENCE_SETTING: self.audience.value,
            CONTACT_SETTING: str(self.contact_id) if self.contact_id else "",
        }
