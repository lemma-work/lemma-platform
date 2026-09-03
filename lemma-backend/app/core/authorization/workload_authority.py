"""What a named delegated workload may do, and whose authority bounds it.

PS-ACCESS-020: a workload acting on a person's behalf gets "the intersection of
that person's access and its own grants, and never the union". Two things must
both hold, and either one alone refuses:

1. the workload holds an explicit resource grant for the action, inside its own
   pod and delegation scope (or the invoking person approved the action for
   this session), and
2. the invoking person could perform the same action on the same resource
   themselves -- the ceiling in :func:`_invoker_ceiling_decision`, which runs
   the ordinary USER evaluation for them.

A grant is therefore a *ceiling on the workload*, never a promotion for the
person driving it: a POD_VIEWER who invokes an agent granted
``datastore.record.write`` still cannot write through it. It is also what stops
a delegation outliving its person -- someone removed from a pod holds nothing,
so the intersection is empty on their very next request (PS-ACCESS-023).

**Headless runs.** A run with no invoking person is authorized on the
workload's grants alone: there is no second set to intersect with, and refusing
everything would break every scheduled and event-driven run. Note that almost
nothing is actually invoker-less today -- a schedule, webhook or datastore
event fires as the person who owns the schedule (``ScheduleStartService``
builds *their* user context and requires ``agent.execute`` before dispatch), so
the ceiling applies to automation too. The genuinely person-less shape is the
AGENT/FUNCTION actor type, built by ``build_workload_context``, which never
reaches this module. ``ctx.user_id is None`` here is the defensive form of the
same rule, so a future person-less delegation degrades to grants-only rather
than to a blanket denial.

**The default pod agent** is the opposite shape and never reaches this module:
it holds no grants and mirrors the invoking person's pod permissions, so it is
already bounded by them (see ``clamp_to_pod`` in ``Authorizer.authorize``). Who
may *trigger* a workload at all is a separate question, governed by
``agent.execute`` / ``function.execute`` grants on the workload itself.

Extracted from ``service.py`` rather than written there because that file is
already over the size ceiling, and because this is one coherent rule with one
entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import (
    ActorType,
    AuthorizationDecision,
    Context,
    ResourceRef,
    ResourceVisibility,
)
from app.core.authorization.permissions import equivalent_permission_ids

if TYPE_CHECKING:  # type-only — ``service`` imports this module, so at runtime
    from app.core.authorization.service import Authorizer  # this would cycle


async def authorize_delegated_workload(
    authorizer: "Authorizer",
    ctx: Context,
    permission_id: str,
    resource: ResourceRef,
) -> AuthorizationDecision:
    """Decide one action for a named delegated workload. See the module docstring.

    DESTRUCTIVE_ACTIONS are the carve-out on the grant half, gated earlier in
    ``Authorizer.authorize``: with no explicit grant they deny with
    DESTRUCTIVE_ACTION_REQUIRES_APPROVAL unless the person recorded a session
    approval for the action type. That does not lift the ceiling below -- a
    person cannot approve, for a workload, what they could not do themselves.
    """
    if ctx.delegation_scope and not (
        equivalent_permission_ids(permission_id) & ctx.delegation_scope
    ):
        # Implication-expanded so a {function.execute} scope also covers
        # the implied function.read a run needs.
        return AuthorizationDecision(
            False,
            "DELEGATION_SCOPE_VIOLATION",
            permission_id,
            resource,
        )
    if resource.pod_id is not None and resource.pod_id != ctx.pod_id:
        return AuthorizationDecision(
            False, "POD_SCOPE_MISMATCH", permission_id, resource
        )
    if resource.organization_id is not None and resource.pod_id is None:
        # Workload grants are pod rows; org-scoped resources fall back to the
        # invoking person's role capability — already the intersection, since
        # the workload half is empty by construction here.
        if not ctx.has_permission(permission_id):
            return AuthorizationDecision(
                False,
                "INSUFFICIENT_PERMISSION",
                permission_id,
                resource,
            )
        if resource.organization_id != ctx.organization_id:
            return AuthorizationDecision(
                False,
                "ORG_SCOPE_MISMATCH",
                permission_id,
                resource,
            )
        return AuthorizationDecision(True, "ORG_VISIBLE", permission_id, resource)

    visibility = resource.visibility or ResourceVisibility.POD
    if (
        visibility == ResourceVisibility.PERSONAL
        and resource.owner_user_id != ctx.user_id
    ):
        # Privacy trumps grants: nothing grants a workload access to
        # another user's PERSONAL resource.
        return AuthorizationDecision(
            False,
            "PERSONAL_RESOURCE_DENIED",
            permission_id,
            resource,
        )

    # The ceiling, before the grant half — so the session-approval escape hatch
    # below is bounded by it too, and so a refusal caused by the person is never
    # reported as a missing workload grant (whose fix, "grant the workload
    # more", would not help).
    ceiling = await _invoker_ceiling_decision(authorizer, ctx, permission_id, resource)
    if ceiling is not None:
        return ceiling

    workload_grant_ids = await authorizer._matching_grant_ids_for_principal_sets(  # noqa: SLF001
        ctx,
        permission_id,
        resource,
        (ctx.workload_principal_refs,),
    )
    if not workload_grant_ids:
        # A session approval (APPROVE_FOR_SESSION) stands in as an ephemeral
        # grant for anything the person chose to approve for the session.
        # (Destructive actions are already gated in ``authorize``; by the time
        # an ungranted destructive action reaches here it must carry an
        # approval — but check generically so any approved action is honored.)
        #
        # Imported here rather than at module scope, because ``service``
        # imports this module and the two would cycle. Only the ungranted path
        # pays the lookup, and it is the one the destructive gate shares.
        from app.core.authorization.service import _session_approval

        if await _session_approval(
            ctx,
            session_id=ctx.delegation_session_id,
            workload_actor_id=ctx.actor_id,
            permission_id=permission_id,
        ):
            return AuthorizationDecision(
                True, "SESSION_APPROVAL", permission_id, resource
            )
        return AuthorizationDecision(
            False,
            "MISSING_WORKLOAD_RESOURCE_GRANT",
            permission_id,
            resource,
            resource_name=await authorizer._describe_resource(resource),  # noqa: SLF001
        )

    return _granted_decision(
        ctx, permission_id, resource, visibility, workload_grant_ids
    )


def _granted_decision(
    ctx: Context,
    permission_id: str,
    resource: ResourceRef,
    visibility: ResourceVisibility,
    workload_grant_ids: list[UUID],
) -> AuthorizationDecision:
    """Which allow reason a matched grant earns, by the resource's visibility.

    Split out so the reason mapping stays readable beside the rule that reached
    it, and so the entry point above stays one decision per branch.
    """
    matched = tuple(workload_grant_ids)
    if visibility == ResourceVisibility.PUBLIC:
        return AuthorizationDecision(
            True, "PUBLIC_RESOURCE", permission_id, resource, matched_grant_ids=matched
        )
    if resource.owner_user_id is not None and resource.owner_user_id == ctx.user_id:
        return AuthorizationDecision(
            True, "RESOURCE_OWNER", permission_id, resource, matched_grant_ids=matched
        )
    if visibility == ResourceVisibility.POD:
        return AuthorizationDecision(
            True, "POD_VISIBLE", permission_id, resource, matched_grant_ids=matched
        )
    if visibility == ResourceVisibility.RESTRICTED:
        return AuthorizationDecision(
            True,
            "WORKLOAD_RESOURCE_GRANT",
            permission_id,
            resource,
            matched_grant_ids=matched,
        )
    return AuthorizationDecision(
        False, "UNSUPPORTED_VISIBILITY", permission_id, resource
    )


async def _invoker_ceiling_decision(
    authorizer: "Authorizer",
    ctx: Context,
    permission_id: str,
    resource: ResourceRef,
) -> AuthorizationDecision | None:
    """Refuse what the invoking person could not do themselves, or ``None``.

    ``None`` means "the ceiling is not in the way" — either there is no invoking
    person (a headless run) or they hold the action on this resource — and the
    caller carries on to the workload's own grants.

    The person's half is evaluated by running the *ordinary* USER authorization
    for them rather than by re-deriving a rule here. That is deliberate: their
    access comes from role permissions, resource ownership, visibility and their
    own grants together, and any rule written out again beside those would drift
    from them. Recursion is not a risk — the mirrored context is a USER with no
    workload principals, so it cannot re-enter this module.

    Asked through ``authorizer.authorize`` rather than ``invoker.can``: the
    ``Context`` wrapper commits to hand the pooled connection back once a
    decision is reached, and this one runs *inside* another decision, half-way
    through the caller's own transaction.
    """
    invoker = _invoking_user_context(authorizer, ctx)
    if invoker is None:
        return None
    if (await authorizer.authorize(invoker, permission_id, resource)).allowed:
        return None
    return AuthorizationDecision(
        False,
        "DELEGATION_EXCEEDS_INVOKER",
        permission_id,
        resource,
        resource_name=await authorizer._describe_resource(resource),  # noqa: SLF001
    )


def _invoking_user_context(authorizer: "Authorizer", ctx: Context) -> Context | None:
    """``ctx`` as the invoking person alone, or ``None`` for a headless run.

    Built from the person's own half of the delegated context — the
    ``invoker_*`` fields and ``permission_ids``, which
    ``build_delegated_workload_context`` deliberately keeps unmerged — so
    nothing the workload holds leaks into the ceiling.

    Not cached: the object is a dataclass, and the query behind the decision is
    already memoized on the authorizer, which this one shares.
    """
    if ctx.user_id is None:
        return None
    return Context(
        actor_type=ActorType.USER,
        actor_id=str(ctx.user_id),
        authorizer=authorizer,
        request_id=ctx.request_id,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        pod_id=ctx.pod_id,
        role_names=ctx.invoker_role_names,
        permission_ids=ctx.permission_ids,
        principal_refs=ctx.invoker_principal_refs,
        grant_principal_sets=(ctx.invoker_principal_refs,),
        pod_is_deleted=ctx.pod_is_deleted,
    )
