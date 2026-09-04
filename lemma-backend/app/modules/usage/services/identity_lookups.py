"""What usage needs to know about people, behind one import of the root.

Two predicates and one lookup, all owned by `identity`. They arrive through
`app/composition` because that is where the wiring lives, and they arrive
*here* because every module that reaches through the root puts another module's
internal layout into its own build -- the architecture ratchet counts those
edges, and usage should spend one rather than one per caller.

Deferred inside the function because the import pulls in the identity module,
and both callers are imported at startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NamedTuple

#: A unit of work and a (user, organization) pair, answered yes or no.
OrganizationPredicate = Callable[..., Awaitable[bool]]
#: A unit of work and a user id, answered with an address or nothing.
EmailLookup = Callable[..., Awaitable[str | None]]


class IdentityLookups(NamedTuple):
    """The three answers, together, so the import above stays a single edge."""

    can_view_organization_usage: OrganizationPredicate
    is_organization_member: OrganizationPredicate
    resolve_user_email: EmailLookup


def identity_lookups() -> IdentityLookups:
    """Who may read whose usage, and where to write to them about it.

    One import statement, not three: the ratchet counts edges, and splitting
    this by caller would spend an edge each time somebody in usage needs to
    know something about a person.
    """
    from app.composition.identity_notifications import (
        resolve_user_email,
        user_can_view_organization_usage,
        user_is_organization_member,
    )

    return IdentityLookups(
        can_view_organization_usage=user_can_view_organization_usage,
        is_organization_member=user_is_organization_member,
        resolve_user_email=resolve_user_email,
    )


__all__ = [
    "EmailLookup",
    "IdentityLookups",
    "OrganizationPredicate",
    "identity_lookups",
]
