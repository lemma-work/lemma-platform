"""What identity an organization may claim: its name, its slug, its domain.

An organization's *slug* is globally unique; its display name is a label and
may be shared (PS-ONB-014). Onboarding used to walk that ladder from the browser
— up to twenty sequential creates on the critical path of a signup, with the
workspace renaming itself under the user while they watched — because a derived
name has no one to arbitrate a collision. This does the walk in one call
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

# How many numbered handles to try before falling back to one that cannot
# collide. Small on purpose: past a handful, ``acme-7`` is no more recognisable
# than a suffixed one, and each rung costs a query.
READABLE_ATTEMPTS = 5

# Suffixed candidates are 24 bits of hex against a namespace of one organization.
# Bounded anyway, because an unbounded loop in a signup path is a hang.
SUFFIXED_ATTEMPTS = 5


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
        # Said as what the person can do next, with a code a client can branch
        # on: the domain is not theirs to take, but joining that organization,
        # or making one people join by invitation, both still are.
        raise OrganizationConflictError(
            "An organization already uses this email domain. Join it, or "
            "create one that people join by invitation.",
            code=OrganizationConflictError.DOMAIN_TAKEN,
        )
    return owner_domain


GetBySlug = Callable[[str], Awaitable[Any | None]]


async def assign_organization_identity(
    entity: Any,
    *,
    get_by_slug: GetBySlug,
    resolve_conflicts: bool,
) -> None:
    """Settle ``entity``'s name and slug, or refuse.

    The name always stands, and only the slug is ever checked: two
    organizations may both be called "Acme". A handle derived from the name
    moves aside when it is taken; a slug the user did type conflicts loudly,
    because silently creating ``acme-2`` for someone who asked for ``acme``
    would be worse than telling them.

    ``resolve_conflicts`` once walked the *name* for a workspace onboarding
    named -- "Personal 2", "Personal 5" -- writing a counter onto somebody's
    workspace that they never chose and that says nothing about them. Moving
    the handle instead is what it now shares with every other create, so the
    flag no longer changes anything here; it stays on the wire for callers
    that send it.
    """
    del resolve_conflicts

    # Whether the person typed a handle, asked before it is derived from the
    # name. It decides who the conflict below belongs to.
    handle_was_chosen = bool(str(entity.slug or "").strip())

    entity.slug = normalize_organization_slug(entity.slug, entity.name)
    if not await get_by_slug(entity.slug):
        return

    if handle_was_chosen:
        # They asked for this handle by name. Handles are scarce and this one is
        # gone; say so rather than quietly seating them somewhere else.
        raise OrganizationConflictError(
            "Organization slug already exists",
            code=OrganizationConflictError.SLUG_TAKEN,
        )

    # They typed only a display name, and the handle was derived from it.
    # Refusing here refuses the *name* -- which PS-ONB-014 says is theirs to
    # share, because names are how people recognise their own organization and
    # not how the system tells organizations apart. So the name stands and the
    # handle moves.
    entity.slug = await _free_handle_near(entity.slug, get_by_slug)


async def _free_handle_near(base: str, get_by_slug: GetBySlug) -> str:
    """A free handle as close to ``base`` as possible.

    ``acme`` taken becomes ``acme-2``, then ``acme-3``. Past a handful the
    numbering stops being recognisable, so it falls back to a suffix that
    cannot realistically collide.
    """
    for attempt in range(2, READABLE_ATTEMPTS + 2):
        candidate = f"{base}-{attempt}"
        if not await get_by_slug(candidate):
            return candidate

    for _ in range(SUFFIXED_ATTEMPTS):
        candidate = f"{base}-{uuid4().hex[:6]}"
        if not await get_by_slug(candidate):
            return candidate

    raise OrganizationConflictError(
        "Could not find an available organization handle",
        code=OrganizationConflictError.SLUG_TAKEN,
    )
