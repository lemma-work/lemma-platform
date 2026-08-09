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
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceChannelRoute,
    SurfaceLifecycleKind,
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


class SurfaceConfigurationMixin(SurfaceConfigurationAuthorizationMixin):
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

        kind = str(setup.get("kind") or "")
        candidates, user_id, authorized = (
            await self._authorized_configuration_surfaces(
                request,
                tenant_id=setup.get("tenant_id"),
                platform=platform,
                actor_external_user_id=setup.get("actor_external_user_id"),
                adapter=adapter,
                action=self._configuration_action(kind),
            )
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
                'agent_surfaces.ingress_service.surface_channel_setup_handling.diagnostic',
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
            await self._send_starter_prompt(adapter, credentials, setup)
        elif kind == "open_dm":
            await self._open_dm_setup(adapter, credentials, setup, surface, ctx)
        elif kind == "submit_dm":
            await self._submit_dm_setup(adapter, credentials, setup, surface, ctx)
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
        await adapter.send_starter_prompt(
            credentials=credentials,
            user_id=str(setup.get("actor_external_user_id") or ""),
            prompt=str(setup.get("prompt") or ""),
        )

    async def _open_dm_setup(self, adapter, credentials, setup, surface, ctx) -> None:
        agents = await self._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_READ
        )
        await adapter.open_dm_agent_modal(
            credentials=credentials,
            trigger_id=str(setup.get("trigger_id") or ""),
            agent_names=[agent.name for agent in agents],
            current=surface.config.slack.choice_for_user(
                setup.get("actor_external_user_id")
            ),
            surface_id=str(surface.id),
        )

    async def _submit_dm_setup(self, adapter, credentials, setup, surface, ctx) -> None:
        agent_name = await self._validated_agent_choice(
            surface=surface,
            ctx=ctx,
            agent_name=setup.get("agent_name"),
            action=Permissions.AGENT_READ,
        )
        if setup.get("agent_name") and agent_name is None:
            return
        external_user_id = str(setup.get("actor_external_user_id") or "")
        await self._set_dm_agent_for_user(
            surface=surface,
            external_user_id=external_user_id,
            agent_name=agent_name,
        )
        await self._publish_home(
            surface=surface,
            adapter=adapter,
            credentials=credentials,
            external_user_id=external_user_id,
            ctx=ctx,
        )

    async def _open_channel_setup(
        self, adapter, credentials, setup, surface, ctx, channel_id
    ) -> None:
        agents = await self._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_UPDATE
        )
        await adapter.open_channel_setup_modal(
            credentials=credentials,
            trigger_id=str(setup.get("trigger_id") or ""),
            channel_id=channel_id,
            channel_label=await adapter.channel_name(
                credentials=credentials, channel_id=channel_id
            ),
            agent_names=[agent.name for agent in agents],
            surface_id=str(surface.id),
        )

    async def _submit_channel_setup(
        self, adapter, credentials, setup, surface, ctx, channel_id
    ) -> None:
        agent_name = await self._validated_agent_choice(
            surface=surface,
            ctx=ctx,
            agent_name=setup.get("agent_name"),
            action=Permissions.AGENT_UPDATE,
        )
        if setup.get("agent_name") and agent_name is None:
            return
        await self._route_channel_to_agent(
            surface=surface, channel_id=channel_id, agent_name=agent_name
        )
        await adapter.send_channel_setup_prompt(
            credentials=credentials,
            channel_id=channel_id,
            user_id=str(setup.get("actor_external_user_id") or ""),
            confirmed_agent=agent_name or "the pod assistant",
        )

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
        self, *, surface, adapter, credentials, external_user_id: str, ctx
    ) -> None:
        """Render the Home tab for one viewer."""
        if not external_user_id:
            return
        agents = await self._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_READ
        )
        pod = await self.uow.session.get(Pod, surface.pod_id)
        await adapter.publish_home_view(
            credentials=credentials,
            user_id=external_user_id,
            pod_name=str(getattr(pod, "name", "") or "") or None,
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

        action = (
            Permissions.AGENT_UPDATE
            if parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
            else Permissions.AGENT_READ
        )
        candidates, user_id, authorized = (
            await self._authorized_configuration_surfaces(
                request,
                tenant_id=parsed.tenant_id,
                platform=platform,
                actor_external_user_id=parsed.actor_external_user_id,
                adapter=adapter,
                action=action,
            )
        )
        explicit_surface_id = (
            str(surface.id) if surface is not None else None
        )
        selected = await self._pick_configuration_surface(
            authorized,
            explicit_surface_id=explicit_surface_id,
            user_id=user_id,
            platform=platform,
        )
        if selected is None:
            if not candidates or not parsed.actor_external_user_id:
                return True
            prompt_surface = authorized[0][0] if authorized else candidates[0]
            credentials = await self._resolve_credentials(prompt_surface)
            if not authorized:
                if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
                    await adapter.publish_home_view(
                        credentials=credentials,
                        user_id=parsed.actor_external_user_id,
                        pod_name=None,
                        dm_agent_name=None,
                        channel_routes=[],
                        agents=[],
                        apps=[],
                        access_message=(
                            "You need access to a connected Lemma pod before this app can show agents or settings."
                        ),
                    )
                elif (
                    parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
                    and parsed.external_channel_id
                ):
                    await adapter.send_channel_setup_prompt(
                        credentials=credentials,
                        channel_id=parsed.external_channel_id,
                        user_id=parsed.actor_external_user_id,
                        configuration_error=(
                            "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                        ),
                    )
                return True
            choices = await self._surface_choice_labels(authorized)
            if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
                await adapter.publish_home_view(
                    credentials=credentials,
                    user_id=parsed.actor_external_user_id,
                    pod_name=None,
                    dm_agent_name=None,
                    channel_routes=[],
                    agents=[],
                    apps=[],
                    surface_choices=choices,
                )
            elif (
                parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
                and parsed.external_channel_id
            ):
                await adapter.send_channel_setup_prompt(
                    credentials=credentials,
                    channel_id=parsed.external_channel_id,
                    user_id=parsed.actor_external_user_id,
                    surface_choices=choices,
                )
            return True
        surface, ctx = selected
        try:
            await self._handle_lifecycle_event(
                surface=surface, parsed=parsed, ctx=ctx
            )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_lifecycle_handling.diagnostic',
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _handle_lifecycle_event(
        self,
        *,
        surface,
        parsed: ParsedSurfaceLifecycleEvent,
        ctx,
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
                ctx=ctx,
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
