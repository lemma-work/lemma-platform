"""Identity, membership, and agent authorization for in-platform setup flows."""

from __future__ import annotations

from uuid import UUID

from aiohttp import ClientError
from slack_sdk.errors import SlackApiError

from app.core.authorization.delegation import is_pod_default_agent
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.factory import create_authorization_data_service
from app.modules.pod.contracts.members import pod_name
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)


class SurfaceConfigurationAuthorizationMixin:
    async def _configuration_surface_candidates(self, request, *, tenant_id, platform):
        candidates = await self.surface_repository.list_active_by_type(platform)
        receiver_surface_ids = getattr(request, "receiver_surface_ids", None)
        receiver_ids = set(receiver_surface_ids or [])
        if receiver_surface_ids is not None:
            candidates = [s for s in candidates if s.id in receiver_ids]
        if tenant_id:
            candidates = [
                surface
                for surface in candidates
                if str(surface.external_workspace_id or "") == str(tenant_id)
            ]
        return candidates

    async def _resolve_configuration_actor(
        self, *, candidates, actor_external_user_id: str | None, adapter
    ):
        actor = str(actor_external_user_id or "").strip()
        if not self._can_resolve_configuration_actor(actor, candidates):
            return None
        first = candidates[0]
        existing = await self.external_user_repository.get_by_identity(
            platform=first.surface_type.value,
            tenant_id=first.external_workspace_id,
            external_user_id=actor,
        )
        if existing is not None and existing.resolved_user_id is not None:
            return existing.resolved_user_id
        event = ParsedInboundSurfaceEvent(
            platform=first.surface_type,
            conversation_type=ConversationType.EXTERNAL_DM,
            tenant_id=first.external_workspace_id,
            external_thread_id=f"configuration:{actor}",
            sender_external_user_id=actor,
            message_text="",
            is_dm=True,
        )
        # Credentials are resolved first, inside the session; the profile fetch
        # that follows is an HTTP call to the platform, so the connection goes
        # back for it. Only reads have happened at this point, so the release is
        # a plain commit -- `safe_to_release` declines it otherwise.
        credentials = await self._resolve_credentials(first)
        try:
            async with connection_released(self.uow.session):
                profile = await adapter.fetch_sender_profile(
                    credentials=credentials, event=event
                )
        except SlackApiError, ClientError:
            profile = None
        resolved = await self.identity_service.resolve(
            event=event, sender_profile=profile
        )
        return resolved.internal_user_id

    def _can_resolve_configuration_actor(self, actor: str, candidates) -> bool:
        return bool(
            actor
            and candidates
            and self.external_user_repository is not None
            and self.identity_service is not None
        )

    async def _authorized_configuration_surfaces(
        self,
        request,
        *,
        tenant_id,
        platform,
        actor_external_user_id: str | None,
        adapter,
        action: str,
    ):
        candidates = await self._configuration_surface_candidates(
            request, tenant_id=tenant_id, platform=platform
        )
        user_id = await self._resolve_configuration_actor(
            candidates=candidates,
            actor_external_user_id=actor_external_user_id,
            adapter=adapter,
        )
        if user_id is None:
            return candidates, None, []
        member_pod_ids = await self._configuration_member_pod_ids(user_id)
        authorized = []
        for surface in candidates:
            if surface.pod_id not in member_pod_ids:
                continue
            ctx = await create_authorization_data_service(self.uow).build_user_context(
                user_id=user_id, pod_id=surface.pod_id
            )
            if await self._can_configure_surface(
                surface=surface, ctx=ctx, action=action
            ):
                authorized.append((surface, ctx))
        return candidates, user_id, authorized

    async def _configuration_member_pod_ids(self, user_id) -> set:
        if self.pod_membership_port is None:
            return set()
        return set(await self.pod_membership_port.get_user_pod_ids(user_id))

    async def _can_configure_surface(self, *, surface, ctx, action: str) -> bool:
        if not await ctx.can(action):
            return False
        # Same rule as everywhere else: the assistant is pod-scoped. Without
        # this, giving it a row would newly require a grant on it to configure
        # the pod's own mailbox -- a tightening nobody asked for.
        if is_pod_default_agent(surface.agent_id, pod_id=surface.pod_id):
            return True
        return await ctx.can(
            action,
            ResourceRef(
                resource_type=ResourceType.AGENT,
                resource_id=surface.agent_id,
                pod_id=surface.pod_id,
            ),
        )

    async def _pick_configuration_surface(
        self,
        authorized,
        *,
        explicit_surface_id: str | None,
        user_id,
        platform,
    ):
        if explicit_surface_id:
            try:
                selected_id = UUID(str(explicit_surface_id))
            except ValueError:
                return None
            return next(
                (entry for entry in authorized if entry[0].id == selected_id), None
            )
        if len(authorized) == 1:
            return authorized[0]
        if user_id is None or self.pod_membership_port is None:
            return None
        default_id = await self.pod_membership_port.get_user_default_surface_id(
            user_id, str(getattr(platform, "value", platform))
        )
        return next((entry for entry in authorized if entry[0].id == default_id), None)

    async def _surface_choice_labels(self, authorized) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for surface, _ in authorized:
            label = await pod_name(self.uow.session, surface.pod_id) or surface.name
            choices.append((label, str(surface.id)))
        return choices

    async def _visible_agents(self, *, surface, ctx, action: str):
        agents, _ = await self.conversation_service.agent_repository.list_by_pod(
            pod_id=surface.pod_id
        )
        visible = []
        for agent in agents:
            if await self._can_access_agent(
                surface=surface, ctx=ctx, agent=agent, action=action
            ):
                visible.append(agent)
        return visible

    async def _validated_agent_choice(
        self, *, surface, ctx, agent_name: str | None, action: str
    ) -> str | None:
        if not agent_name:
            return None
        agent = await self.conversation_service.agent_repository.get_by_pod_and_name(
            pod_id=surface.pod_id, name=agent_name
        )
        if agent is None or not await self._can_access_agent(
            surface=surface, ctx=ctx, agent=agent, action=action
        ):
            return None
        return agent.name

    async def _can_access_agent(self, *, surface, ctx, agent, action: str) -> bool:
        return await ctx.can(
            action,
            ResourceRef(
                resource_type=ResourceType.AGENT,
                resource_id=agent.id,
                pod_id=surface.pod_id,
            ),
        )
