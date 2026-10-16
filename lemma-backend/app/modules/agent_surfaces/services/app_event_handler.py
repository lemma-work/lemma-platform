"""Events about the app itself rather than about a message.

The third kind of inbound event: neither a message nor an answer to one, but the
platform telling us its own situation changed -- the bot was added to a channel,
somebody opened the app's home, somebody submitted a settings modal. None of it
reaches an agent, and all of it ends either in a configuration change or in an
explanation of why one cannot be made.

Configuration and lifecycle were two classes, and the reason they could not be
untangled is that they are one responsibility. `SurfaceConfigurationMixin`
inherited `SurfaceLifecycleMixin`, and lifecycle called back into
the home tab, which is configuration's -- a cycle if they are layers, and not
a cycle at all once they stop pretending to be. They are one object here.

The authorization half *is* separable and is `ConfigurationAccess`: it answers
which surfaces this person may point at an agent and which agents they may
choose, and has no edge back into any of this.
"""

from __future__ import annotations

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from app.modules.agent_surfaces.domain.channel_setup import (
    ChannelSetupKind,
    ChannelSetupRequest,
)

from uuid import UUID


from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session_uow import commit_now
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import (
    in_chat_setup,
    InChatSetup,
    SurfacePlatformAdapterPort,
)
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
from app.modules.agent_surfaces.services.app_home import (
    AppHomeView,
    surface_agent_name,
)
from app.modules.agent_surfaces.services.configuration_prompt import (
    prompt_for_configuration,
)
from app.modules.agent_surfaces.services.group_hello import say_hello_in_group
from app.modules.agent_surfaces.services.group_registry import (
    adopt_connected_channel,
    adopt_joined_group,
)
from app.modules.agent_surfaces.services.configuration_access import (
    ConfigurationAccess,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)

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
        self.home = AppHomeView(
            uow=uow,
            access=access,
            external_user_repository=external_user_repository,
        )

    async def try_handle_channel_setup(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Open the "who answers here?" modal, or persist what it returned.

        Returns True when the payload belonged to this flow, so the caller stops
        — none of it reaches an agent. A platform with no in-chat set-up has no
        such payloads.
        """
        found, platform = self._adapter_for_request(request)
        if found is None:
            return False
        adapter = in_chat_setup(found)
        if adapter is None:
            return False
        # Egress: connection back for the platform round trip. Every
        # `connection_released` below is the same idea.
        async with connection_released(self.uow.session):
            setup = await adapter.parse_channel_setup(request.payload, request.headers)
        if setup is None:
            return False

        (
            candidates,
            user_id,
            authorized,
        ) = await self.access._authorized_configuration_surfaces(
            request,
            tenant_id=setup.tenant_id,
            platform=platform,
            actor_external_user_id=setup.actor_external_user_id,
            adapter=found,
            action=self._configuration_action(setup.kind),
        )
        selected = await self.access._pick_configuration_surface(
            authorized,
            explicit_surface_id=setup.surface_id,
            user_id=user_id,
            platform=platform,
        )

        if setup.kind is ChannelSetupKind.SELECT_SURFACE:
            await self._handle_surface_selection(
                selected=selected, user_id=user_id, adapter=adapter, setup=setup
            )
            return True

        if selected is None:
            await self._prompt_for_unselected_configuration(
                adapter=adapter,
                setup=setup,
                candidates=candidates,
                authorized=authorized,
            )
            return True
        surface, ctx = selected
        credentials = await self.credential_resolver.for_surface(surface)
        try:
            await self._dispatch_configuration_action(
                setup=setup,
                surface=surface,
                ctx=ctx,
                adapter=adapter,
                credentials=credentials,
            )
        except SQLAlchemyError as exc:
            raise_if_database_unavailable(exc)
            logger.warning(
                "agent_surfaces.app_event_handler.channel_setup_failed.failed",
                surface_id=str(surface.id),
                kind=setup.kind.value,
                exc_info=True,
            )
            await self._report_setup_not_saved(
                adapter=adapter, credentials=credentials, setup=setup
            )
        return True

    async def _report_setup_not_saved(
        self,
        *,
        adapter: InChatSetup,
        credentials: SurfaceCredentials,
        setup: ChannelSetupRequest,
    ) -> None:
        """Say so in the channel when a submitted setting was not saved.

        The modal has already closed by the time the write fails, so without
        this the person is left believing the channel is configured.
        """
        actor = setup.actor_external_user_id
        if (
            setup.kind is not ChannelSetupKind.SUBMIT
            or not setup.channel_id
            or not actor
        ):
            return
        await self.uow.rollback()
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=setup.channel_id,
                user_id=actor,
                configuration_error=(
                    "Lemma could not save that setting. Try again in a moment."
                ),
            )

    @staticmethod
    def _configuration_action(kind: ChannelSetupKind) -> str:
        return (
            Permissions.AGENT_UPDATE
            if kind in {ChannelSetupKind.OPEN, ChannelSetupKind.SUBMIT}
            else Permissions.AGENT_READ
        )

    async def _handle_surface_selection(
        self,
        *,
        selected: tuple[AgentSurfaceEntity, Context] | None,
        user_id: UUID | None,
        adapter: InChatSetup,
        setup: ChannelSetupRequest,
    ) -> None:
        if selected is None or user_id is None:
            return
        surface, ctx = selected
        await self.pod_membership_port.set_user_default_surface_id(
            user_id, surface.surface_type.value, surface.id
        )
        await self.uow.commit()
        await self.home.show_to(
            surface=surface,
            adapter=adapter,
            credentials=await self.credential_resolver.for_surface(surface),
            external_user_id=setup.actor_external_user_id or "",
            ctx=ctx,
        )

    async def _prompt_for_unselected_configuration(
        self,
        *,
        adapter: InChatSetup,
        setup: ChannelSetupRequest,
        candidates: list[AgentSurfaceEntity],
        authorized: list[tuple[AgentSurfaceEntity, Context]],
    ) -> None:
        actor = setup.actor_external_user_id
        if not setup.channel_id or not actor or not candidates:
            return
        prompt_surface = authorized[0][0] if authorized else candidates[0]
        credentials = await self.credential_resolver.for_surface(prompt_surface)
        choices = (
            await self.access._surface_choice_labels(authorized)
            if len(authorized) > 1
            else None
        )
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=setup.channel_id,
                user_id=actor,
                surface_choices=choices,
                configuration_error=(
                    None
                    if authorized
                    else "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                ),
            )

    async def _dispatch_configuration_action(
        self,
        *,
        setup: ChannelSetupRequest,
        surface: AgentSurfaceEntity,
        ctx: Context,
        adapter: InChatSetup,
        credentials: SurfaceCredentials,
    ) -> None:
        if setup.kind is ChannelSetupKind.STARTER_PROMPT:
            async with connection_released(self.uow.session):
                await adapter.send_starter_prompt(
                    credentials=credentials,
                    user_id=setup.actor_external_user_id or "",
                    prompt=setup.prompt,
                )
        elif setup.kind is ChannelSetupKind.OPEN:
            await self._open_channel_setup(adapter, credentials, setup, surface)
        else:
            await self._submit_channel_setup(adapter, credentials, setup, surface, ctx)

    async def _open_channel_setup(
        self,
        adapter: InChatSetup,
        credentials: SurfaceCredentials,
        setup: ChannelSetupRequest,
        surface: AgentSurfaceEntity,
    ) -> None:
        agent_name = await surface_agent_name(self.uow, surface)
        # Committed, not only released: resolving who clicked may have upserted
        # their external-user row, and `connection_released` declines to let go
        # of a session with writes in it. This runs inside the webhook request,
        # so the pooled connection would be held across `views.open`.
        await commit_now(self.uow)
        async with connection_released(self.uow.session):
            await adapter.open_channel_setup_modal(
                credentials=credentials,
                trigger_id=setup.trigger_id,
                channel_id=setup.channel_id,
                channel_label=await adapter.channel_name(
                    credentials=credentials, channel_id=setup.channel_id
                ),
                agent_name=agent_name,
                surface_id=str(surface.id),
            )

    async def _submit_channel_setup(
        self,
        adapter: InChatSetup,
        credentials: SurfaceCredentials,
        setup: ChannelSetupRequest,
        surface: AgentSurfaceEntity,
        ctx: Context,
    ) -> None:
        agent_name = await surface_agent_name(self.uow, surface)
        await self._allow_channel(
            surface=surface,
            channel_id=setup.channel_id,
            owner_user_id=getattr(ctx, "user_id", None),
        )
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=setup.channel_id,
                user_id=setup.actor_external_user_id or "",
                confirmed_agent=agent_name,
            )

    async def _allow_channel(
        self, *, surface, channel_id: str, owner_user_id: UUID | None = None
    ) -> None:
        """Add one channel to the list this surface's agent answers in.

        It used to point the channel at a chosen agent. A surface has one agent
        now, so a channel is a place rather than a choice, and adding it twice
        is the same as adding it once. The channel is one of the pod's groups
        from here on, and whoever connected it answers for any people outside
        the pod in it.
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
        await adopt_connected_channel(
            self.uow,
            surface=surface,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
        )
        await self.uow.commit()

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
        except SQLAlchemyError as exc:
            raise_if_database_unavailable(exc)
            logger.warning(
                "agent_surfaces.app_event_handler.lifecycle_event_failed.failed",
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
        setup = in_chat_setup(adapter)
        if setup is None or not candidates or not parsed.actor_external_user_id:
            return
        prompt_surface = authorized[0][0] if authorized else candidates[0]
        credentials = await self.credential_resolver.for_surface(prompt_surface)
        choices = (
            await self.access._surface_choice_labels(authorized) if authorized else None
        )
        await prompt_for_configuration(
            self.uow.session,
            adapter=setup,
            parsed=parsed,
            actor_external_user_id=parsed.actor_external_user_id,
            credentials=credentials,
            surface_choices=choices,
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
        setup = in_chat_setup(adapter)

        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            # Slack spins forever until a view is published, so this must answer
            # every open — including the very first, before anything is set up.
            if not parsed.actor_external_user_id or setup is None:
                return
            await self.home.show_to(
                surface=surface,
                adapter=setup,
                credentials=credentials,
                external_user_id=parsed.actor_external_user_id,
                ctx=ctx,
            )
            return

        if parsed.kind is not SurfaceLifecycleKind.JOINED_CHANNEL:
            return
        if not parsed.actor_external_user_id or not parsed.external_channel_id:
            return
        created = await adopt_joined_group(
            self.uow, surface=surface, parsed=parsed, owner_user_id=ctx.user_id
        )
        if created is not None:
            if created:
                await say_hello_in_group(
                    self.uow,
                    surface=surface,
                    adapter=adapter,
                    credentials=credentials,
                    channel_id=parsed.external_channel_id,
                )
            return
        if setup is None or surface.channel_route_for(
            channel_id=parsed.external_channel_id, channel_name=""
        ):
            return
        async with connection_released(self.uow.session):
            await setup.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=parsed.external_channel_id,
                user_id=parsed.actor_external_user_id,
                channel_name=await setup.channel_name(
                    credentials=credentials, channel_id=parsed.external_channel_id
                ),
            )
