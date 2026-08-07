"""Configuring a surface from inside the chat app itself.

These handle the third kind of inbound event: neither a message nor an answer to
one, but the platform telling us its own situation changed — the bot was added
to a channel, someone opened the app's home, someone submitted a settings modal.
None of it reaches an agent.

Kept out of :mod:`ingress_service` because that module is about getting a
person's message to an agent and the reply back; this is about the settings
around it.
"""

from __future__ import annotations


from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.factory import create_authorization_data_service
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceChannelRoute,
    SurfaceLifecycleKind,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)

logger = get_logger(__name__)


class SurfaceConfigurationMixin:
    """Set-up flows a person drives from inside the chat app."""

    async def try_handle_channel_setup(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Open the "who answers here?" modal, or persist what it returned.

        Returns True when the payload belonged to this flow, so the caller stops
        — none of it reaches an agent.
        """
        adapter, platform = self._adapter_for_request(request)
        if adapter is None:
            return False
        setup = await adapter.parse_channel_setup(request.payload, request.headers)
        if setup is None:
            return False

        surface = await self._surface_for_workspace(
            request, tenant_id=setup.get("tenant_id"), platform=platform
        )
        if surface is None:
            return True
        credentials = await self._resolve_credentials(surface)
        channel_id = str(setup.get("channel_id") or "")
        try:
            if setup.get("kind") == "starter_prompt":
                await adapter.send_starter_prompt(
                    credentials=credentials,
                    user_id=str(setup.get("actor_external_user_id") or ""),
                    prompt=str(setup.get("prompt") or ""),
                )
                return True

            if setup.get("kind") == "open_dm":
                agents, _ = await self.conversation_service.agent_repository.list_by_pod(
                    pod_id=surface.pod_id
                )
                await adapter.open_dm_agent_modal(
                    credentials=credentials,
                    trigger_id=str(setup.get("trigger_id") or ""),
                    agent_names=[agent.name for agent in agents],
                    current=surface.config.slack.choice_for_user(
                        setup.get("actor_external_user_id")
                    ),
                )
                return True

            if setup.get("kind") == "submit_dm":
                await self._set_dm_agent_for_user(
                    surface=surface,
                    external_user_id=str(setup.get("actor_external_user_id") or ""),
                    agent_name=setup.get("agent_name"),
                )
                await self._publish_home(
                    surface=surface,
                    adapter=adapter,
                    credentials=credentials,
                    external_user_id=str(setup.get("actor_external_user_id") or ""),
                )
                return True

            if setup.get("kind") == "open":
                agents, _ = await self.conversation_service.agent_repository.list_by_pod(
                    pod_id=surface.pod_id
                )
                await adapter.open_channel_setup_modal(
                    credentials=credentials,
                    trigger_id=str(setup.get("trigger_id") or ""),
                    channel_id=channel_id,
                    channel_label=await adapter.channel_name(
                        credentials=credentials, channel_id=channel_id
                    ),
                    agent_names=[agent.name for agent in agents],
                )
                return True

            agent_name = setup.get("agent_name")
            await self._route_channel_to_agent(
                surface=surface,
                channel_id=channel_id,
                agent_name=agent_name,
            )
            # The modal just closes on save, so without this the person has no
            # idea whether anything happened — and no way to see what they set.
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=channel_id,
                user_id=str(setup.get("actor_external_user_id") or ""),
                confirmed_agent=agent_name or "the pod assistant",
            )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_channel_setup_handling.diagnostic',
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _set_dm_agent_for_user(
        self,
        *,
        surface,
        external_user_id: str,
        agent_name: str | None,
    ) -> None:
        """Record who answers one person's DMs.

        Choosing the pod assistant stores a sentinel rather than removing the
        entry — absence means "never chose", which resolves to the surface
        default agent, and that is a different answer.
        """
        if not external_user_id:
            return
        chosen = dict(surface.config.slack.dm_agent_by_user)
        chosen[external_user_id] = (
            agent_name or surface.config.slack.POD_ASSISTANT
        )
        surface.config.slack.dm_agent_by_user = chosen
        await self.surface_repository.update(surface)
        await self.uow.commit()

    async def _publish_home(
        self, *, surface, adapter, credentials, external_user_id: str
    ) -> None:
        """Render the Home tab for one viewer."""
        if not external_user_id:
            return
        agents, _ = await self.conversation_service.agent_repository.list_by_pod(
            pod_id=surface.pod_id
        )
        await adapter.publish_home_view(
            credentials=credentials,
            user_id=external_user_id,
            pod_name=None,
            dm_agent_name=(
                None
                if surface.config.slack.chose_pod_assistant(external_user_id)
                else (
                    surface.config.slack.agent_for_user(external_user_id)
                    or await self._agent_name_for_agent_id(surface.agent_id)
                )
            ),
            channel_routes=[
                (
                    route.channel_id,
                    None if route.use_pod_assistant else route.agent_name,
                )
                for route in surface.config.channels
                if route.channel_id
            ],
            agents=[(agent.name, agent.description) for agent in agents],
            apps=await self._home_apps(surface=surface, external_user_id=external_user_id),
            workspace_url=str(getattr(settings, "frontend_url", "") or "") or None,
            logo_url=surface_settings.slack_home_logo_url,
        )

    async def _home_apps(self, *, surface, external_user_id: str) -> list:
        """Apps this *viewer* may open — never the pod's full list.

        Visibility is per-user, so an unresolvable Slack identity gets no apps
        rather than everyone else's.
        """
        try:
            from app.modules.apps.contracts import list_ready_pod_apps

            external = (
                await self.external_user_repository.get_by_identity(
                    platform="SLACK",
                    tenant_id=surface.external_workspace_id,
                    external_user_id=external_user_id,
                )
                if self.external_user_repository
                else None
            )
            resolved_user_id = getattr(external, "resolved_user_id", None)
            if resolved_user_id is None:
                return []
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(user_id=resolved_user_id, pod_id=surface.pod_id)
            apps = await list_ready_pod_apps(
                uow=self.uow, pod_id=surface.pod_id, ctx=auth_ctx
            )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_home_apps.diagnostic',
                surface_id=str(surface.id),
            )
            return []
        domain = str(getattr(settings, "app_base_domain", "") or "").strip()
        if not domain:
            return []
        return [(app.name, f"https://{app.public_slug}.{domain}") for app in apps]

    async def _route_channel_to_agent(
        self,
        *,
        surface,
        channel_id: str,
        agent_name: str | None,
    ) -> None:
        """Point one channel at one agent, replacing any existing route.

        ``agent_name=None`` means the pod's own assistant — which is what an
        empty ``agent_name`` on a route already resolves to, so the two agree
        without a special case downstream.
        """
        if not channel_id:
            return
        routes = [
            route
            for route in surface.config.channels
            if str(route.channel_id or "") != channel_id
        ]
        routes.append(
            SurfaceChannelRoute(
                channel_id=channel_id,
                agent_name=agent_name,
                use_pod_assistant=agent_name is None,
            )
        )
        surface.config.channels = routes
        await self.surface_repository.update(surface)
        await self.uow.commit()

    def _adapter_for_request(self, request):
        if isinstance(request, SurfaceDirectWebhookIngress):
            return None, None
        platform = self._resolve_platform(request.source)
        return (self.adapter_registry.get(platform) if platform else None), platform

    async def _surface_for_workspace(self, request, *, tenant_id, platform):
        candidates = await self.surface_repository.list_active_by_type(platform)
        receiver_ids = set(getattr(request, "receiver_surface_ids", None) or [])
        if receiver_ids:
            candidates = [s for s in candidates if s.id in receiver_ids]
        if tenant_id:
            scoped = [
                surface
                for surface in candidates
                if str(surface.external_workspace_id or "") == str(tenant_id)
            ]
            if scoped:
                return scoped[0]
        return candidates[0] if len(candidates) == 1 else None

    async def try_handle_lifecycle(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Parse + route an event about the app itself rather than a message.

        Returns True when the payload was a lifecycle event, so the caller stops
        — these never become conversations. False means fall through to the
        interaction and message paths.
        """
        surface = None
        if isinstance(request, SurfaceDirectWebhookIngress):
            surface = await self.surface_repository.get(request.surface_id)
            if surface is None:
                return False
            adapter = self.adapter_registry.get(surface.surface_type)
            platform = surface.surface_type
        else:
            platform = self._resolve_platform(request.source)
            adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return False
        parsed = await adapter.parse_inbound_lifecycle(
            request.payload, request.headers
        )
        if parsed is None:
            return False

        if surface is None:
            surface = await self._surface_for_lifecycle(request, parsed, platform)
        if surface is None:
            # Nothing installed for this workspace — nothing to configure.
            return True
        try:
            await self._handle_lifecycle_event(surface=surface, parsed=parsed)
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_lifecycle_handling.diagnostic',
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _surface_for_lifecycle(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
        parsed: ParsedSurfaceLifecycleEvent,
        platform,
    ):
        """The installed surface this lifecycle event belongs to.

        Matches on the workspace id the event carries, so one deployment serving
        many Slack workspaces configures the right one.
        """
        candidates = await self.surface_repository.list_active_by_type(platform)
        receiver_ids = set(
            getattr(request, "receiver_surface_ids", None) or []
        )
        if receiver_ids:
            candidates = [s for s in candidates if s.id in receiver_ids]
        if parsed.tenant_id:
            scoped = [
                surface
                for surface in candidates
                if str(surface.external_workspace_id or "") == str(parsed.tenant_id)
            ]
            if scoped:
                return scoped[0]
        return candidates[0] if len(candidates) == 1 else None

    async def _handle_lifecycle_event(
        self,
        *,
        surface,
        parsed: ParsedSurfaceLifecycleEvent,
    ) -> None:
        """React to the app's own situation changing.

        Today the one reaction is offering to configure a freshly joined
        channel. A channel that already has a route needs no prompt — the
        invite was someone re-adding the bot, not setting it up.
        """
        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return
        credentials = await self._resolve_credentials(surface)

        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            # Slack spins forever until a view is published, so this must answer
            # every open — including the very first, before anything is set up.
            if not parsed.actor_external_user_id:
                return
            await self._publish_home(
                surface=surface,
                adapter=adapter,
                credentials=credentials,
                external_user_id=parsed.actor_external_user_id,
            )
            return

        if parsed.kind is not SurfaceLifecycleKind.JOINED_CHANNEL:
            return
        if not parsed.actor_external_user_id or not parsed.external_channel_id:
            return
        if surface.channel_route_for(
            channel_id=parsed.external_channel_id, channel_name=""
        ):
            return
        await adapter.send_channel_setup_prompt(
            credentials=credentials,
            channel_id=parsed.external_channel_id,
            user_id=parsed.actor_external_user_id,
            channel_name=await adapter.channel_name(
                credentials=credentials, channel_id=parsed.external_channel_id
            ),
        )

