"""Events about the app itself rather than about a message.

The third kind of inbound event: neither a message nor an answer to one, but the
platform telling us its own situation changed -- the bot was added to a channel,
somebody opened the app's home, somebody submitted a settings modal. None of it
reaches an agent, and all of it ends either in a configuration change or in an
explanation of why one cannot be made.

Configuration and lifecycle were two classes, and the reason they could not be
untangled is that they are one responsibility. `SurfaceConfigurationMixin`
inherited `SurfaceLifecycleMixin`, and lifecycle called back into
`_publish_home`, which is configuration's -- a cycle if they are layers, and not
a cycle at all once they stop pretending to be. They are one object here.

The authorization half *is* separable and is `ConfigurationAccess`: it answers
which surfaces this person may point at an agent and which agents they may
choose, and has no edge back into any of this.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.core.authorization.context import Context
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedSurfaceLifecycleEvent,
    SurfaceChannelRoute,
    SurfaceLifecycleKind,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.configuration_access import (
    ConfigurationAccess,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.pod.contracts.members import pod_name

logger = get_logger(__name__)


class AppEventHandler:
    """Set-up and lifecycle events a person drives from inside the chat app."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        adapter_registry: SurfacePlatformAdapterRegistry,
        credential_resolver: SurfaceCredentialResolver,
        pod_membership_port: SurfacePodMembershipPort,
        external_user_repository: ExternalSurfaceUserRepository,
        access: ConfigurationAccess,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.adapter_registry = adapter_registry
        self.credential_resolver = credential_resolver
        # Held here as well as on `access`, and for a different job each time:
        # this sets the person's default surface and reads the identity behind
        # an App Home open; `access` asks whether they may configure anything.
        self.pod_membership_port = pod_membership_port
        self.external_user_repository = external_user_repository
        self.access = access

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
        (
            candidates,
            user_id,
            authorized,
        ) = await self.access._authorized_configuration_surfaces(
            request,
            tenant_id=setup.get("tenant_id"),
            platform=platform,
            actor_external_user_id=setup.get("actor_external_user_id"),
            adapter=adapter,
            action=self._configuration_action(kind),
        )
        selected = await self.access._pick_configuration_surface(
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
        credentials = await self.credential_resolver.for_surface(surface)
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
        if selected is None or user_id is None:
            return
        surface, ctx = selected
        await self.pod_membership_port.set_user_default_surface_id(
            user_id, surface.surface_type.value, surface.id
        )
        await self.uow.commit()
        await self._publish_home(
            surface=surface,
            adapter=adapter,
            credentials=await self.credential_resolver.for_surface(surface),
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
                credentials=await self.credential_resolver.for_surface(prompt_surface),
                channel_id=channel_id,
                user_id=actor,
                surface_choices=(
                    await self.access._surface_choice_labels(authorized)
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
        agents = await self.access._visible_agents(
            surface=surface, ctx=ctx, action=Permissions.AGENT_READ
        )
        name_of_pod = await pod_name(self.uow.session, surface.pod_id)
        # Egress: the pod and agent rows are read above; the connection
        # goes back before the view is pushed to the platform.
        async with connection_released(self.uow.session):
            await adapter.publish_home_view(
                credentials=credentials,
                user_id=external_user_id,
                pod_name=name_of_pod or None,
                agent_name=await self._surface_agent_name(surface),
                # The channels this bot answers in. Ids alone: each entry used
                # to name the agent routed to that channel, and there is one
                # agent now, so the Home tab states it once above the list.
                channel_ids=[
                    route.channel_id
                    for route in surface.config.channels
                    if route.channel_id
                ],
                agents=[(agent.name, agent.description) for agent in agents],
                apps=await self._home_apps(
                    surface=surface,
                    external_user_id=external_user_id,
                    auth_ctx=ctx,
                ),
                workspace_url=settings.frontend_url or None,
                logo_url=surface_settings.slack_home_logo_url,
            )

    async def _home_apps(
        self, *, surface, external_user_id: str, auth_ctx=None
    ) -> list[tuple[str, str]]:
        """Apps this *viewer* may open, as (name, url) — never the pod's full list.

        Visibility is per-user, so an unresolvable Slack identity gets no apps
        rather than everyone else's.
        """
        try:
            from app.modules.apps.contracts import list_ready_pod_apps

            if auth_ctx is None:
                external = await self.external_user_repository.get_by_identity(
                    platform="SLACK",
                    tenant_id=surface.external_workspace_id,
                    external_user_id=external_user_id,
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
        domain = settings.app_base_domain.strip()
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
        agent = await agent_conversations.surface_agent_identity(
            self.uow, surface.agent_id
        )
        if agent is None or agent.is_pod_default:
            return DEFAULT_RESPONDER_NAME
        return agent.name

    def _adapter_for_request(self, request):
        if isinstance(request, SurfaceDirectWebhookIngress):
            return None, None
        platform = platform_value_for_source(request.source)
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
            platform = platform_value_for_source(request.source)
            adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return False

        # Parsing can reach the platform (Slack parses locally, but Teams and
        # Telegram fetch a profile or a token here), so treat it as egress.
        async with connection_released(self.uow.session):
            parsed = await adapter.parse_inbound_lifecycle(
                request.payload, request.headers
            )
        if parsed is None:
            return False

        (
            candidates,
            user_id,
            authorized,
        ) = await self.access._authorized_configuration_surfaces(
            request,
            tenant_id=parsed.tenant_id,
            platform=platform,
            actor_external_user_id=parsed.actor_external_user_id,
            adapter=adapter,
            action=(
                Permissions.AGENT_UPDATE
                if parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
                else Permissions.AGENT_READ
            ),
        )
        selected = await self.access._pick_configuration_surface(
            authorized,
            explicit_surface_id=str(surface.id) if surface is not None else None,
            user_id=user_id,
            platform=platform,
        )
        if selected is None:
            await self._answer_unroutable_lifecycle(
                adapter=adapter,
                parsed=parsed,
                candidates=candidates,
                authorized=authorized,
            )
            return True

        surface, ctx = selected
        try:
            await self._handle_lifecycle_event(surface=surface, parsed=parsed, ctx=ctx)
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.ingress_service.surface_lifecycle_handling.diagnostic",
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _answer_unroutable_lifecycle(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedSurfaceLifecycleEvent,
        candidates: list[AgentSurfaceEntity],
        authorized: list[tuple[AgentSurfaceEntity, Context]],
    ) -> None:
        """Say something useful when the event cannot be pinned to one surface.

        Two reasons it cannot: the actor may edit nothing here, or they may edit
        several pods and have not said which. Both are answered in the same two
        places -- the App Home, or the channel they just joined -- so the only
        difference is what is said.
        """
        if not candidates or not parsed.actor_external_user_id:
            return
        prompt_surface = authorized[0][0] if authorized else candidates[0]
        credentials = await self.credential_resolver.for_surface(prompt_surface)
        choices = (
            await self.access._surface_choice_labels(authorized) if authorized else None
        )
        await self._prompt_for_configuration(
            adapter=adapter,
            parsed=parsed,
            actor_external_user_id=parsed.actor_external_user_id,
            credentials=credentials,
            surface_choices=choices,
        )

    async def _prompt_for_configuration(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedSurfaceLifecycleEvent,
        actor_external_user_id: str,
        credentials: dict[str, Any],
        surface_choices: list[tuple[str, str]] | None,
    ) -> None:
        """Ask which pod, or explain why nothing can be shown.

        ``surface_choices`` of None means the actor is not authorized anywhere,
        which is the only thing that changes between the two messages.

        The actor is a parameter rather than read back off ``parsed``, where it
        is ``str | None``: the caller has already refused an event without one,
        and taking it here is how that guarantee crosses the boundary.
        """
        no_access = surface_choices is None
        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            async with connection_released(self.uow.session):
                await adapter.publish_home_view(
                    credentials=credentials,
                    user_id=actor_external_user_id,
                    pod_name=None,
                    # No surface has been chosen on this path, so there is no
                    # agent whose name this could be. It was omitted entirely
                    # until the port declared the operation, and `agent_name`
                    # has no default on any implementation -- so the one screen
                    # that tells somebody they have no access to a connected pod
                    # raised `TypeError` and rendered nothing at all.
                    agent_name=DEFAULT_RESPONDER_NAME,
                    channel_ids=[],
                    agents=[],
                    apps=[],
                    surface_choices=surface_choices,
                    access_message=(
                        "You need access to a connected Lemma pod before this app can show agents or settings."
                        if no_access
                        else None
                    ),
                )
            return

        if (
            parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
            and parsed.external_channel_id
        ):
            async with connection_released(self.uow.session):
                await adapter.send_channel_setup_prompt(
                    credentials=credentials,
                    channel_id=parsed.external_channel_id,
                    user_id=actor_external_user_id,
                    surface_choices=surface_choices,
                    configuration_error=(
                        "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                        if no_access
                        else None
                    ),
                )

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
        credentials = await self.credential_resolver.for_surface(surface)

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
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=parsed.external_channel_id,
                user_id=parsed.actor_external_user_id,
                channel_name=await adapter.channel_name(
                    credentials=credentials, channel_id=parsed.external_channel_id
                ),
            )
