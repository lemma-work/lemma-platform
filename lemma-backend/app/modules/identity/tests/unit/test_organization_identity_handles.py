"""Who a handle conflict belongs to.

PS-ONB-014 says two organizations may carry the same display name, because a
name is how people recognise their own organization and not how the system
tells organizations apart. Dropping the unique index on ``name`` was not enough
on its own: the handle is *derived* from the name when the person did not type
one, so "Acme" still collided with "Acme" -- on a value they never chose, with
a message about a field they never filled in.

So the rule these tests hold is about authorship, not about scarcity: a handle
the person typed conflicts loudly, and a handle the system derived moves aside.
"""

from __future__ import annotations

import pytest

from app.modules.identity.domain.errors import OrganizationConflictError
from app.modules.identity.domain.organization_entities import OrganizationEntity
from app.modules.identity.domain.organization_identity import (
    assign_organization_identity,
)


def _taken(*slugs: str):
    held = set(slugs)

    async def get_by_slug(slug: str):
        return object() if slug in held else None

    return get_by_slug


@pytest.mark.asyncio
async def test_a_derived_handle_moves_aside_so_the_name_can_be_shared():
    entity = OrganizationEntity(name="Acme", slug="")

    await assign_organization_identity(
        entity, get_by_slug=_taken("acme"), resolve_conflicts=False
    )

    assert entity.name == "Acme", "the name the person typed must survive"
    assert entity.slug == "acme-2", entity.slug


@pytest.mark.asyncio
async def test_it_keeps_walking_while_handles_are_taken():
    entity = OrganizationEntity(name="Acme", slug="")

    await assign_organization_identity(
        entity,
        get_by_slug=_taken("acme", "acme-2", "acme-3"),
        resolve_conflicts=False,
    )

    assert entity.slug == "acme-4", entity.slug


@pytest.mark.asyncio
async def test_a_handle_the_person_typed_still_conflicts_loudly():
    # The other half of the rule. Seating someone at `acme-2` when they asked
    # for `acme` would be worse than telling them, because the handle is the
    # address they will hand out.
    entity = OrganizationEntity(name="Acme", slug="acme")

    with pytest.raises(OrganizationConflictError) as exc:
        await assign_organization_identity(
            entity, get_by_slug=_taken("acme"), resolve_conflicts=False
        )

    assert exc.value.code == OrganizationConflictError.SLUG_TAKEN


@pytest.mark.asyncio
async def test_a_free_handle_is_left_exactly_as_derived():
    entity = OrganizationEntity(name="Acme", slug="")

    await assign_organization_identity(
        entity, get_by_slug=_taken(), resolve_conflicts=False
    )

    assert entity.slug == "acme", entity.slug
