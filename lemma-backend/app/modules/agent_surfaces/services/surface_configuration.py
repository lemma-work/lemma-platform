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

from app.modules.agent.contracts import AgentKind
from app.modules.agent_surfaces.platforms.slack.blocks import (
    DEFAULT_RESPONDER_NAME,
)
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.modules.agent_surfaces.services.surface_lifecycle import (
    SurfaceLifecycleMixin,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    SurfaceChannelRoute,
)
from app.composition.surface_identity import Pod
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.services.surface_configuration_authorization import (
    SurfaceConfigurationAuthorizationMixin,
)

logger = get_logger(__name__)


class SurfaceConfigurationMixin(
    SurfaceLifecycleMixin, SurfaceConfigurationAuthorizationMixin
):
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
        # Egress: connection back for the platform round trip. Every
        # `connection_released` below is the same idea.
        async with connection_released(self.uow.session):
            setup = await adapter.parse_channel_setup(request.payload, request.headers)
        if setup is None:
            return False

        kind = str(setup.get("kind") or "")
        candidates, user_id, authorized = await self._authorized_configuration_surfaces(
            request,
            tenant_id=setup.get("tenant_id"),
            platform=platform,
            actor_external_user_id=setup.get("actor_external_user_id"),
            adapter=adapter,
            action=self._configuration_action(kind),
        )
        selected = await self._pick_configuration_surface(
            authorized,
            explicit_surface_id=setup.get("surface_id"),
            user_id=user_id,
            platform=platform,
        )
        channel_id = str(setup.get("channel_id") or "")

        if kind == "select_surface":
            await self._handle_surface_selection(
                selected=selected,
                user_id=user_id,
                adapter=adapter,
                setup=setup,
            )
            return True

        if selected is None:
            await self._prompt_for_unselected_configuration(
                adapter=adapter,
                setup=setup,
                channel_id=channel_id,
                candidates=candidates,
                authorized=authorized,
            )
            return True
        surface, ctx = selected
        credentials = await self._resolve_credentials(surface)
        try:
            await self._dispatch_configuration_action(
                kind=kind,
                setup=setup,
                surface=surface,
                ctx=ctx,
                adapter=adapter,
                credentials=credentials,
                channel_id=channel_id,
            )
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.ingress_service.surface_channel_setup_handling.diagnostic",
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    @staticmethod
    def _configuration_action(kind: str) -> str:
        return (
            Permissions.AGENT_UPDATE
            if kind in {"open", "submit"}
            else Permissions.AGENT_READ
        )

    async def _handle_surface_selection(
        self, *, selected, user_id, adapter, setup
    ) -> None:
        if selected is None or user_id is None or self.pod_membership_port is None:
            return
        surface, ctx = selected
        await self.pod_membership_port.set_user_default_surface_id(
            user_id, surface.surface_type.value, surface.id
        )
        await self.uow.commit()
        await self._publish_home(
            surface=surface,
            adapter=adapter,
            credentials=await self._resolve_credentials(surface),
            external_user_id=str(setup.get("actor_external_user_id") or ""),
            ctx=ctx,
        )

    async def _prompt_for_unselected_configuration(
        self, *, adapter, setup, channel_id, candidates, authorized
    ) -> None:
        actor = str(setup.get("actor_external_user_id") or "")
        if not channel_id or not actor or not candidates:
            return
        prompt_surface = authorized[0][0] if authorized else candidates[0]
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=await self._resolve_credentials(prompt_surface),
                channel_id=channel_id,
                user_id=actor,
                surface_choices=(
                    await self._surface_choice_labels(authorized)
                    if len(authorized) > 1
                    else None
                ),
                configuration_error=(
                    None
                    if authorized
                    else "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                ),
            )

    async def _dispatch_configuration_action(
        self, *, kind, setup, surface, ctx, adapter, credentials, channel_id
    ) -> None:
        if kind == "starter_prompt":
            async with connection_released(self.uow.session):
                await self._send_starter_prompt(adapter, credentials, setup)
        elif kind == "open":
            await self._open_channel_setup(
                adapter, credentials, setup, surface, ctx, channel_id
            )
        else:
            await self._submit_channel_setup(
                adapter, credentials, setup, surface, ctx, channel_id
            )

    @staticmethod
    async def _send_starter_prompt(adapter, credentials, setup) -> None:
        """Pure egress. The caller releases the connection around this."""
        await adapter.send_starter_prompt(
            credentials=credentials,
            user_id=str(setup.get("actor_external_user_id") or ""),
            prompt=str(setup.get("prompt") or ""),
        )

    async def _open_channel_setup(
        self, adapter, credentials, setup, surface, ctx, channel_id
    ) -> None:
        agent_name = await self._surface_agent_name(surface)
        async with connection_released(self.uow.session):
            await adapter.open_channel_setup_modal(
                credentials=credentials,
                trigger_id=str(setup.get("trigger_id") or ""),
                channel_id=channel_id,
                channel_label=await adapter.channel_name(
                    credentials=credentials, channel_id=channel_id
                ),
                agent_name=agent_name,
                surface_id=str(surface.id),
            )

    async def _submit_channel_setup(
        self, adapter, credentials, setup, surface, ctx, channel_id
    ) -> None:
        agent_name = await self._surface_agent_name(surface)
        await self._allow_channel(surface=surface, channel_id=channel_id)
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=channel_id,
                user_id=str(setup.get("actor_external_user_id") or ""),
                confirmed_agent=agent_name,
            )

    async def _publish_home(
        self, *, surface, adapter, credentials, external_user_id: str, ctx
    ) -> None:
        """Render the Home tab for one viewer."""
        if not external_user_id:
            return
        agents = await self._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_READ
        )
        pod = await self.uow.session.get(Pod, surface.pod_id)
        # Egress: the pod and agent rows are read above; the connection
        # goes back before the view is pushed to the platform.
        async with connection_released(self.uow.session):
            await adapter.publish_home_view(
                credentials=credentials,
                user_id=external_user_id,
                pod_name=str(getattr(pod, "name", "") or "") or None,
                agent_name=await self._surface_agent_name(surface),
                # The channels this bot answers in. The second element used to
                # be the agent routed to that channel; there is one agent now,
                # so it is the same for every row and the Home tab says it once.
                channel_routes=[
                    (route.channel_id, None)
                    for route in surface.config.channels
                    if route.channel_id
                ],
                agents=[(agent.name, agent.description) for agent in agents],
                apps=await self._home_apps(
                    surface=surface,
                    external_user_id=external_user_id,
                    auth_ctx=ctx,
                ),
                workspace_url=str(getattr(settings, "frontend_url", "") or "") or None,
                logo_url=surface_settings.slack_home_logo_url,
            )

    async def _home_apps(
        self, *, surface, external_user_id: str, auth_ctx=None
    ) -> list:
        """Apps this *viewer* may open — never the pod's full list.

        Visibility is per-user, so an unresolvable Slack identity gets no apps
        rather than everyone else's.
        """
        try:
            from app.modules.apps.contracts import list_ready_pod_apps

            if auth_ctx is None:
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
                "agent_surfaces.ingress_service.surface_home_apps.diagnostic",
                surface_id=str(surface.id),
            )
            return []
        domain = str(getattr(settings, "app_base_domain", "") or "").strip()
        if not domain:
            return []
        return [(app.name, f"https://{app.public_slug}.{domain}") for app in apps]

    async def _allow_channel(self, *, surface, channel_id: str) -> None:
        """Add one channel to the list this surface's agent answers in.

        It used to point the channel at a chosen agent. A surface has one agent
        now, so a channel is a place rather than a choice, and adding it twice
        is the same as adding it once.
        """
        if not channel_id:
            return
        routes = [
            route
            for route in surface.config.channels
            if str(route.channel_id or "") != channel_id
        ]
        routes.append(SurfaceChannelRoute(channel_id=channel_id))
        surface.config.channels = routes
        await self.surface_repository.update(surface)
        await self.uow.commit()

    async def _surface_agent_name(self, surface) -> str:
        """What this surface's agent is called in front of a person."""
        agent = await self.conversation_service.agent_repository.get(surface.agent_id)
        if agent is None or agent.kind is AgentKind.POD_DEFAULT:
            return DEFAULT_RESPONDER_NAME
        return agent.name

    def _adapter_for_request(self, request):
        if isinstance(request, SurfaceDirectWebhookIngress):
            return None, None
        platform = self._resolve_platform(request.source)
        return (self.adapter_registry.get(platform) if platform else None), platform
