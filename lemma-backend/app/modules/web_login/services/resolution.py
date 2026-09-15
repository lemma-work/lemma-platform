"""Whose saved login a run may use, and whether it may use one at all.

The shape is taken from `connectors/services/account_resolution_service.py`,
because the question is the same one: a workload is asking to act with a
credential that belongs to a person, and the answer turns on whose authority the
run carries rather than on what the agent asked for.

Two rules, and the second is narrower than the connector one on purpose.

**A run uses only the logins of the person it is running for.** A saved session
is a browser signed in as somebody. There is no equivalent of a shared team
mailbox here: a session is one human's identity at a site, and lending it to
another person's run would mean that site seeing actions it will attribute to
the owner, with nothing at the site's end able to tell the difference. So there
is no "pinned login" and no borrowing -- `delegated_by_user_id` decides, and the
owner is the only answer.

There is deliberately only *one* permission. An earlier version also declared
`web_login.manage`, granted it to pod admins and marked it destructive -- and
nothing could ever have checked it, because there is no operation it would
guard: the routes that add and remove a saved login act on the caller's own,
and lending one to another person is refused by construction above. A
permission that names a capability the system does not have is worse than no
permission, because a role template showing it says somebody has it.

**The permission is checked, not described.** An earlier version of this
declared `web_login.use`, made `web_login.manage` destructive, put both in the
role templates, and never called `require()` anywhere -- so removing the
permission from a role changed nothing at all. The check is in `resolve()`, it
is the only way to reach a secret, and there is a test that fails if a caller
reaches the repository around it.

A departed member is refused by construction rather than by a check: a workload
gets a delegated context only for somebody the pod can still act for, so a
person who has left has no run to borrow.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import get_current_context
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError


class WebLoginAccessDenied(DomainError):
    """This run may not use that saved login."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


async def resolve_owner(
    *,
    auth_ctx: Context | None = None,
    requested_user_id: UUID | None = None,
) -> UUID:
    """Whose logins this run may reach, or raise.

    Returns the owning user id. For a person acting directly that is
    themselves; for a workload it is the person it delegates for, and never
    anybody else.
    """
    ctx = auth_ctx or get_current_context()
    if ctx is None:
        raise WebLoginAccessDenied("No authorization context")

    owner = ctx.delegated_by_user_id or ctx.user_id
    if owner is None:
        raise WebLoginAccessDenied("No person to resolve a saved login for")

    if requested_user_id is not None and requested_user_id != owner:
        # The only way to ask for somebody else's is to name them, and naming
        # them is refused rather than ignored.
        raise WebLoginAccessDenied(
            "A saved login belongs to one person and cannot be used by another"
        )

    # A person acting for themselves needs no grant beyond being themselves --
    # the same early return `account_resolution_service` makes for an owned
    # account. A workload has to hold the permission.
    if ctx.delegated_by_user_id is not None and not ctx.is_user_equivalent:
        # No `ResourceRef`: the permission is pod-scoped and there is no
        # per-login grant to hold. Which login gets used is not a matter of
        # grants at all -- it is decided entirely by whose run this is, above.
        # Passing a ref here would invent a resource nothing else addresses and
        # put `WEB_LOGIN` in the authorizer's hydration path for no gain.
        await ctx.require(Permissions.WEB_LOGIN_USE)
    return owner


__all__ = ["WebLoginAccessDenied", "resolve_owner"]
