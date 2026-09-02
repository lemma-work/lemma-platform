from __future__ import annotations

from secrets import token_hex
from uuid import UUID
from typing import Optional

from app.core.authorization.cache import invalidate_role_snapshot_cache
from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.identity.contracts import OrganizationJoinPolicy, OrganizationRole
from app.modules.icon.contracts import IconCleanupPort
from app.modules.pod.domain.errors import (
    PodAccessDeniedError,
    PodNotFoundError,
    PodValidationError,
)
from app.modules.pod.domain.pod_names import normalize_pod_name
from app.modules.pod.domain.pod_entities import (
    PodEntity,
    PodJoinPolicy,
    PodMemberEntity,
    PodRole,
    PodUpdateEntity,
)
from app.modules.pod.domain.ports import (
    OrganizationMembershipPort,
    PodMemberRepositoryPort,
    PodRepositoryPort,
    PodScheduleTeardownPort,
)
from app.modules.pod.domain.visibility import roles_allow_required
from app.modules.pod.services.pod_role_service import PodRoleService


class PodService:
    def __init__(
        self,
        pod_repository: PodRepositoryPort,
        pod_member_repository: PodMemberRepositoryPort,
        organization_repository: OrganizationMembershipPort,
        pod_role_service: PodRoleService | None = None,
        authorization_service: object | None = None,
        icon_service: IconCleanupPort | None = None,
        schedule_teardown: PodScheduleTeardownPort | None = None,
        uow: object | None = None,
    ):
        self.pod_repository = pod_repository
        self.pod_member_repository = pod_member_repository
        self.organization_repository = organization_repository
        self.pod_role_service = pod_role_service
        self.authorization_service = authorization_service
        self.icon_service = icon_service
        self.schedule_teardown = schedule_teardown
        self._uow = uow

    async def create_pod(self, entity: PodEntity, creator_user_id: UUID) -> PodEntity:
        member = await self.organization_repository.get_member(
            creator_user_id, entity.organization_id
        )
        if not member:
            raise PodAccessDeniedError(
                "User must be a member of the organization to create a pod"
            )

        entity.name = self._normalize_name(entity.name)

        # Aggregate method registers pod.created event.
        entity.mark_created(creator_user_id)

        pod = await self.pod_repository.create(entity)

        pod_member = PodMemberEntity(
            pod_id=pod.id,
            organization_member_id=member.id,
            roles=[PodRole.ADMIN.value],
        )
        created_member = await self.pod_member_repository.create(pod_member)
        if self.pod_role_service is not None:
            await self.pod_role_service.sync_member_roles(
                pod_id=pod.id,
                pod_member_id=created_member.id,
                roles=[PodRole.ADMIN],
                added_by_user_id=creator_user_id,
            )

        # The pod's assistant gets its row here, before anything can look for
        # it. Unlike the mailbox below, this is not best-effort: a pod whose
        # assistant has no row cannot be talked to at all, and the symptom
        # arrives at the first message rather than at creation.
        if self._uow is not None:
            from app.composition.pod_default_agent import provision_pod_default_agent

            # `pod.user_id`, not `creator_user_id`: the migration that backfilled
            # every existing pod read the owner off the pod row, and the two have
            # to agree or a pod made before the change and one made after would
            # attribute their assistant to different people.
            await provision_pod_default_agent(
                self._uow, pod_id=pod.id, user_id=pod.user_id
            )

        # The pod's assistant gets its mailbox here, the way an agent gets one in
        # `create_agent`. It used to be minted on the assistant's first outbound
        # notification instead, which reads as thrift and is not: inbound routes
        # on the address, so until something was sent, mail to the obvious guess
        # matched no surface and started nothing. A pod that cannot be written to
        # is not cheaper than one that can.
        #
        # Best-effort by design, exactly as for an agent: creating a pod must not
        # fail because a mail domain is unset. `_uow` is absent only where a
        # caller built the service without one -- the same guard `delete_pod`
        # uses for its role-cache hook.
        if self._uow is not None:
            from app.composition.agent_email_surface import (
                provision_pod_assistant_email_surface,
            )

            await provision_pod_assistant_email_surface(
                self._uow, pod_id=pod.id, pod_name=pod.name
            )

        return pod

    async def get_pod(
        self, pod_id: UUID, requester_user_id: UUID
    ) -> Optional[PodEntity]:
        pod = await self.pod_repository.get(pod_id)
        if not pod:
            return None

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not org_member:
            raise PodAccessDeniedError("User doesn't have access to this pod")

        # Organization ownership reaches every pod, for reading as for listing
        # and deletion. An editor's reach is pod membership, like everyone
        # else's -- get/list/delete must answer with one rule or the product
        # shows someone pods they may not open, or hides ones they may. See
        # PS-POD-030 and DEV-POD-001.
        if org_member.role == OrganizationRole.ORG_OWNER:
            return pod

        has_access = await self.pod_member_repository.check_user_has_pod_access(
            pod_id, org_member.id
        )
        if not has_access:
            raise PodAccessDeniedError("User doesn't have access to this pod")

        return pod

    async def update_pod(
        self,
        pod_id: UUID,
        data: PodUpdateEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> PodEntity:
        pod_entity = await self.pod_repository.get(pod_id)
        if not pod_entity:
            raise PodNotFoundError()

        if ctx is None:
            raise PodAccessDeniedError("Context is required for pod authorization")
        await ctx.require(
            Permissions.POD_UPDATE, ResourceRef.pod(pod_id, pod_entity.organization_id)
        )

        merged_dict = pod_entity.model_dump()
        update_data = data.model_dump(exclude_unset=True)
        if "name" in update_data and update_data["name"] is not None:
            update_data["name"] = self._normalize_name(update_data["name"])

        # Config is a typed multi-field blob; merge field-wise so a partial
        # update (e.g. only default_runtime, or only join_policy) preserves
        # the other fields instead of resetting them to their defaults.
        if update_data.get("config") is not None:
            merged_config = merged_dict.get("config") or {}
            merged_config.update(update_data["config"])
            # Keep the legacy provider-only key in sync with the full runtime so
            # any code still reading ``default_profile_id`` stays correct.
            default_runtime = merged_config.get("default_runtime")
            if isinstance(default_runtime, dict) and default_runtime.get("profile_id"):
                merged_config["default_profile_id"] = default_runtime["profile_id"]
            update_data["config"] = merged_config
            await self._require_join_policy_authority(
                ctx=ctx,
                pod=pod_entity,
                requested=merged_config.get("join_policy"),
            )
        merged_dict.update(update_data)

        updated_entity = PodEntity(**merged_dict)
        updated = await self.pod_repository.update(updated_entity)

        if (
            self.icon_service
            and "icon_url" in update_data
            and pod_entity.icon_url != updated.icon_url
        ):
            await self.icon_service.delete_by_url(pod_entity.icon_url)

        return updated

    async def _require_join_policy_authority(
        self,
        *,
        ctx: Context,
        pod: PodEntity,
        requested: object,
    ) -> None:
        """Who may change a pod's join policy, and how far they may open it.

        Two separate rules, because the join policy is the one pod setting whose
        effect leaves the pod.

        Changing it at all needs ``pod.member.manage``, not ``pod.update``.
        Deciding who may join is membership management wearing a config field's
        clothes, and ``PUT /pods/{id}`` merges the config field-wise -- so an
        editor could set it in the same request that renamed the pod.

        Opening it to PUBLIC additionally needs the *organization* to be public.
        A PUBLIC pod mints an ORG_MEMBER row for any signed-in account that
        joins it (``_ensure_org_membership_for_join``), so a pod that could
        widen itself past its organization would be a pod-level decision that
        hands out organization membership -- and PS-POD-010 makes the
        organization the outer boundary, owned by the org owner.
        """
        if requested is None:
            return
        try:
            new_policy = PodJoinPolicy(requested)
        except ValueError as exc:
            raise PodValidationError(f"Unknown pod join policy: {requested}") from exc
        if new_policy == pod.config.join_policy:
            return
        await ctx.require(
            Permissions.POD_MEMBER_MANAGE,
            ResourceRef.pod(pod.id, pod.organization_id),
        )
        if new_policy != PodJoinPolicy.PUBLIC:
            return
        organization = await self.organization_repository.get(pod.organization_id)
        org_policy = organization.join_policy if organization else None
        if org_policy != OrganizationJoinPolicy.PUBLIC:
            raise PodAccessDeniedError(
                "This pod cannot be opened to everyone while its organization is "
                "not public. Ask an organization owner to open the organization "
                "first, or use ORG_MEMBERS to open the pod to the organization."
            )

    def _normalize_name(self, name: str) -> str:
        try:
            return normalize_pod_name(name)
        except ValueError as exc:
            raise PodValidationError(str(exc)) from exc

    async def delete_pod(self, pod_id: UUID, requester_user_id: UUID) -> bool:
        # Read through the soft delete, because deleting has to be safe to
        # repeat (PS-POD-050). A client that never saw the first answer sends
        # the request again, and `get` filters `is_deleted` -- so the retry used
        # to come back 404 for a pod the caller had just successfully deleted,
        # which is an error a person has no way to clear. A pod id that was
        # never real is still 404; that is a different answer to a different
        # question.
        pod = await self.pod_repository.get_even_if_deleted(pod_id)
        if not pod:
            raise PodNotFoundError()

        org_member = await self.organization_repository.get_member(
            requester_user_id, pod.organization_id
        )
        if not org_member:
            raise PodAccessDeniedError()

        if org_member.role != OrganizationRole.ORG_OWNER:
            pod_member = await self.pod_member_repository.get_by_pod_and_org_member(
                pod_id, org_member.id
            )
            if not pod_member or not roles_allow_required(
                pod_member.roles,
                PodRole.ADMIN,
            ):
                raise PodAccessDeniedError("Permission denied")

        # Already gone: the permission checks above still ran, so this is not a
        # way to learn a pod exists, and the work below is not repeated. The
        # second call reports the same success as the first.
        if pod.is_deleted:
            return True

        old_icon_url = pod.icon_url
        pod.name = self._build_deleted_pod_name(pod.name)
        pod.mark_deleted()
        await self.pod_repository.update(pod)
        if self.icon_service:
            await self.icon_service.delete_by_url(old_icon_url)
        # The pod's standing work stops with the pod, in this request — not one
        # consumer tick later, and not only if the deletion event survives the
        # queue. A deleted pod that can still report itself armed is the worst
        # kind of runaway: invisible by construction, and billed. See
        # PS-OPS-020, PS-POD-050, DEV-OPS-003.
        if self.schedule_teardown is not None:
            # Unguarded on purpose: this is one UPDATE, so the only thing that
            # reaches here is the database itself failing, and that must abort
            # the deletion rather than be logged under a pod we then report
            # deleted. Swallowing it would recreate the exact state this fix
            # exists to prevent: a gone pod with armed schedules.
            #
            # Disarm, not delete. The rows are what the pod-deleted event needs
            # to find the provider triggers behind them, and tearing those down
            # inline would put an unbounded number of Composio round trips
            # inside this transaction.
            await self.schedule_teardown.disarm_all_for_pod(pod_id)
        # The pod's inbound addresses go with it, in this request, for the same
        # reason its schedules are disarmed here: the pod's *name* is freed
        # above, the moment `_build_deleted_pod_name` renames it. Recreating a
        # pod under that name before the pod-deleted event was consumed asked
        # for an address the deleted pod still held, and got either a suffixed
        # one — with the readable form orphaned on a row nobody can reach — or,
        # once the worker caught up, the deleted pod's own address, still being
        # written to by its correspondents. Which of the two came down to queue
        # lag. Every pod holds an address now, so this stopped being a corner.
        #
        # Only the email surfaces, deliberately. The comment above is right that
        # an unbounded number of Composio round trips must not go inside this
        # transaction — but a Resend surface has none: it receives on a
        # catch-all webhook, so `delete_surface` makes no provider call for it
        # and `_sync_email_schedule` returns early. Bounded by the number of
        # agents in the pod, and the rest still waits for the worker.
        if self._uow is not None:
            from app.composition.agent_email_surface import (
                release_pod_inbound_addresses,
            )

            await release_pod_inbound_addresses(self._uow, pod_id=pod_id)
        # Cached role snapshots outlive the pod otherwise, and they are what
        # authorizes every pod-scoped request. The snapshot carries
        # `pod_is_deleted`, so one written *before* this moment says the pod is
        # alive and keeps saying so until it expires -- which is a deleted
        # pod's records still readable for the length of a cache TTL. Dropped
        # after commit, so a concurrent read cannot re-warm it from the row
        # this transaction has not written yet.
        #
        # Cleared wholesale rather than per member: an organization owner
        # reaches a pod with no membership row at all, so there is no list of
        # affected principals to walk. Deleting a pod is rare, and the cost of
        # over-clearing is re-derivation on next access, never stale authority.
        if self._uow is not None:
            self._uow.after_commit(invalidate_role_snapshot_cache)
        return True

    async def list_pods_by_organization(
        self,
        organization_id: UUID,
        requester_user_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
    ):
        org_member = await self.organization_repository.get_member(
            requester_user_id, organization_id
        )
        if not org_member:
            raise PodAccessDeniedError()
        if org_member.role == OrganizationRole.ORG_OWNER:
            return await self.pod_repository.list_by_org(organization_id, limit, cursor)
        return await self.pod_repository.list_by_org_member(
            organization_id,
            org_member.id,
            limit,
            cursor,
        )

    def _build_deleted_pod_name(self, name: str) -> str:
        suffix = token_hex(4)
        deleted_name = f"deleted-{suffix}-{name}"
        return deleted_name[:255]
