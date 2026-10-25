"""The conferral bound: nobody hands out access they do not themselves hold.

PS-POD-013 and PS-ACCESS-010 state one rule, and until this module existed the
codebase implemented two halves of it, both wrong.

Role *assignment* compared hierarchy ranks (``ROLE_HIERARCHY``), which knows only
the four built-in pod roles -- so every custom role scored zero and cleared any
cap. Role *authorship* compared nothing at all: ``create_or_update_role``
validated that a permission id exists, never that the author holds it. Together
they let a principal mint a role carrying permissions they do not have and hand
it to somebody else.

So the rule lives here once, as a comparison of *permission sets*, and both
paths call it. Ranks cannot express the rule -- a custom role is a set of
permissions, and only a set comparison can bound it.

A superuser is exempt, being outside the model entirely. An organization owner
is not special-cased: their org role carries every pod's admin permissions, so
the ordinary comparison already lets them confer what a pod admin can.

Grants on a resource are bounded by *the resource*, not only by the permission
id (:func:`assert_can_confer_grants`): holding ``folder.read`` somewhere is not
the same as being able to read the RESTRICTED folder being granted, and a grant
handed out on the strength of the former let an agent read what its granter
could not. And a named agent or function confers nothing at all -- an agent
holding ``agent.update`` could otherwise rewrite its own grants.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import (
    Permissions,
    equivalent_permission_ids,
)
from app.core.domain.errors import DomainError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authorization.grants import NormalizedResourceGrant

#: The refusal code every conferral denial carries, so a client can tell
#: "you may not grant this" apart from "you may not be here at all".
CONFERRAL_EXCEEDS_HOLDER = "CONFERRAL_EXCEEDS_HOLDER"


def permissions_not_held(
    held: Collection[str],
    requested: Iterable[str],
) -> list[str]:
    """Requested permission ids that ``held`` cannot satisfy, sorted.

    Satisfaction follows ``equivalent_permission_ids``, the same relation
    ``Context.has_permission`` uses: holding ``app.delete`` satisfies a request
    to confer ``app.read``, because the holder can already do it.
    """
    holder = set(held)
    return sorted(
        {
            permission_id
            for permission_id in requested
            if not (equivalent_permission_ids(permission_id) & holder)
        }
    )


def is_exempt_from_conferral_bound(ctx: Context) -> bool:
    return ctx.is_superuser


def assert_can_confer(
    ctx: Context,
    permission_ids: Iterable[str],
    *,
    action: str,
) -> None:
    """Refuse ``ctx`` any permission in ``permission_ids`` it does not hold.

    ``action`` completes the sentence "You may not <action> ..." and should read
    as what the caller was trying to do ("grant permissions you do not hold").
    """
    if is_exempt_from_conferral_bound(ctx):
        return
    refuse_conferral_beyond(
        held=ctx.permission_ids,
        requested=permission_ids,
        action=action,
    )


def refuse_conferral_beyond(
    *,
    held: Collection[str],
    requested: Iterable[str],
    action: str,
) -> None:
    """The bound applied to a permission set resolved by the caller.

    Separate from :func:`assert_can_confer` because pod role assignment reaches
    the rule without a request ``Context``: it is handed a user id by three
    services, and resolves that user's pod roles itself. Both entry points must
    refuse identically, which is why the comparison and the message are here and
    not duplicated at each site.
    """
    missing = permissions_not_held(held, requested)
    if not missing:
        return
    raise DomainError(
        f"You may not {action}: {', '.join(missing)}. "
        "Ask someone who holds them to make this change.",
        code=CONFERRAL_EXCEEDS_HOLDER,
        status_code=403,
        details={"permission_ids": missing},
    )


async def assert_can_confer_grants(
    ctx: Context,
    grants: Sequence[NormalizedResourceGrant],
    *,
    pod_id: UUID,
    action: str,
) -> None:
    """Refuse a grant the granter could not exercise on that very resource.

    Every grant write goes through here -- the permissions-replace routes, the
    inline ``permissions`` block on create and update, and per-resource sharing
    -- so one rule bounds them all (PS-ACCESS-010). A named agent or function
    may not write grants at all.
    """
    if is_exempt_from_conferral_bound(ctx):
        return
    if ctx.is_named_workload:
        raise DomainError(
            "An agent or function may not change who can do what.",
            code=CONFERRAL_EXCEEDS_HOLDER,
            status_code=403,
        )
    missing: list[str] = []
    for grant in grants:
        resource = ResourceRef(
            resource_type=grant.resource_type,
            resource_id=grant.resource_id,
            pod_id=pod_id,
        )
        if grant.resource_type is ResourceType.CONNECTOR_ACCOUNT and not (
            await ctx.can(Permissions.CONNECTOR_ACCOUNT_SHARE, resource)
        ):
            # A grant on a pod account puts someone's credential in front of
            # the grantee. Being able to use it yourself is not enough -- every
            # pod user can use a POD-visible one -- only its owner may decide
            # who else does (audit H3).
            missing.append(
                f"{Permissions.CONNECTOR_ACCOUNT_SHARE} on {grant.resource_type.value}"
            )
            continue
        for permission_id in grant.permission_ids:
            if not await ctx.can(permission_id, resource):
                missing.append(f"{permission_id} on {grant.resource_type.value}")
    if missing:
        raise DomainError(
            f"You may not {action}: {', '.join(sorted(set(missing)))}. "
            "Ask someone who can do these themselves to make this change.",
            code=CONFERRAL_EXCEEDS_HOLDER,
            status_code=403,
            details={"permission_ids": sorted({m.split(" on ")[0] for m in missing})},
        )


#: A grantee's grants are read whole to tell what a replace adds. Past this
#: many rows the rest count as new and are checked: the bound only tightens.
_MAX_HELD_GRANT_ROWS = 5000


async def assert_can_confer_replacement(
    ctx: Context,
    session: AsyncSession,
    grants: Sequence[NormalizedResourceGrant],
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    action: str,
) -> None:
    """Conferral for a write that replaces a grantee's whole grant set.

    Only what the write *adds* is conferred. A grant the grantee already holds
    is not being handed out again -- someone who could did that -- so it is not
    held against the person saving. Checking the whole set made every grantee
    with one grant beyond an editor's reach uneditable by them: they could not
    even remove a grant, since saving the rest re-sent the one they lacked.
    Removing a grant confers nothing, so it is never refused here.
    """
    from sqlalchemy import select

    from app.core.authorization.grants import NormalizedResourceGrant
    from app.core.authorization.models import ResourcePermissionGrantModel

    rows = await session.execute(
        select(
            ResourcePermissionGrantModel.resource_type,
            ResourcePermissionGrantModel.resource_id,
            ResourcePermissionGrantModel.permission_id,
        )
        .where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
        .limit(_MAX_HELD_GRANT_ROWS)
    )
    held = {
        (str(kind), resource_id, permission) for kind, resource_id, permission in rows
    }
    added: list[NormalizedResourceGrant] = []
    for grant in grants:
        new = [
            permission_id
            for permission_id in grant.permission_ids
            if (grant.resource_type.value, grant.resource_id, permission_id) not in held
        ]
        if new:
            added.append(
                NormalizedResourceGrant(
                    resource_type=grant.resource_type,
                    resource_id=grant.resource_id,
                    permission_ids=new,
                )
            )
    await assert_can_confer_grants(ctx, added, pod_id=pod_id, action=action)
