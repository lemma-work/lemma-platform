"""What identity an organization may claim: its name, its slug, its domain.

Organization names are globally unique. Onboarding used to walk that ladder from
the browser — up to twenty sequential creates on the critical path of a signup,
with the workspace renaming itself under the user while they watched — because a
derived name has no one to arbitrate a collision. This does the walk in one call
instead.

Kept out of ``organization_service`` deliberately: that file is already over the
architecture ratchet's size limit, and both of these are the kind of rule worth
testing without a whole service behind them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from app.modules.identity.domain.email_domains import work_domain_from_email
from app.modules.identity.domain.errors import (
    IdentityValidationError,
    OrganizationConflictError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationJoinPolicy,
)

from app.modules.identity.domain.organization_slugs import (
    normalize_organization_slug,
)

# How many readable names to try before falling back to one that cannot collide.
# Small on purpose: past a handful, "Acme 7" is no better a name than a suffixed
# one, and each rung costs a query.
READABLE_ATTEMPTS = 5

# Suffixed candidates are 24 bits of hex against a namespace of one organization.
# Bounded anyway, because an unbounded loop in a signup path is a hang.
SUFFIXED_ATTEMPTS = 5

IsFree = Callable[[str, str], Awaitable[bool]]


class NoAvailableOrganizationName(Exception):
    """Every candidate was taken. Astronomically unlikely; not silently ignored."""


async def resolve_available_identity(
    base_name: str,
    *,
    is_free: IsFree,
) -> tuple[str, str]:
    """A ``(name, slug)`` pair no organization holds yet.

    ``is_free`` answers for one candidate pair; the caller owns the lookup so
    this stays independent of how organizations are stored.
    """
    for attempt in range(READABLE_ATTEMPTS):
        candidate = base_name if attempt == 0 else f"{base_name} {attempt + 1}"
        slug = normalize_organization_slug("", candidate)
        if await is_free(candidate, slug):
            return candidate, slug

    for _ in range(SUFFIXED_ATTEMPTS):
        candidate = f"{base_name} {uuid4().hex[:6]}"
        slug = normalize_organization_slug("", candidate)
        if await is_free(candidate, slug):
            return candidate, slug

    raise NoAvailableOrganizationName(base_name)


async def resolve_email_domain_for_policy(
    *,
    owner_email: str,
    join_policy: OrganizationJoinPolicy,
    provided_domain: str | None,
    exclude_org_id: UUID | None,
    get_email_domain_org: Callable[[str], Awaitable[Any]],
) -> str | None:
    """Resolve the domain an org claims, enforcing per-domain uniqueness.

    Only ``EMAIL_DOMAIN`` orgs claim a domain (everyone else stores NULL, so
    same-domain users can still create their own orgs). Attempting to claim a
    domain already held by another ``EMAIL_DOMAIN`` org raises a conflict.
    """
    if join_policy != OrganizationJoinPolicy.EMAIL_DOMAIN:
        return None

    owner_domain = work_domain_from_email(owner_email)
    if owner_domain is None:
        raise IdentityValidationError(
            "The EMAIL_DOMAIN join policy requires a work email domain"
        )
    if provided_domain:
        normalized = provided_domain.strip().lower()
        if normalized != owner_domain:
            raise IdentityValidationError(
                "Organization email domain must match the owner's email domain"
            )

    existing_domain = await get_email_domain_org(owner_domain)
    if existing_domain and existing_domain.id != exclude_org_id:
        raise OrganizationConflictError(
            "This email domain is already taken by another organization"
        )
    return owner_domain
