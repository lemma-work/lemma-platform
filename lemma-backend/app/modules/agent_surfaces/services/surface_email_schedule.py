"""The polling schedule behind a Composio-backed email surface.

Gmail and Outlook receive by being polled, so each such surface owns a schedule
that has to be created, kept in step with the surface, and torn down with it.
Resend is an email surface too and has none of this -- it receives over a native
webhook -- which is the distinction every function here turns on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID


from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceEventMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountInfo,
)
from app.modules.connectors.contracts import ConnectorKind
from app.composition.surface_schedule import ScheduleService
from app.modules.schedule.contracts import (
    ScheduleCreateEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)

_EMAIL_TRIGGER_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "GMAIL": "GMAIL_NEW_GMAIL_MESSAGE",
    "OUTLOOK": "OUTLOOK_MESSAGE_TRIGGER",
}

if TYPE_CHECKING:
    from app.core.authorization.context import Context


class SurfaceEmailScheduleMixin:
    """Split out of :class:`AgentSurfaceService`; see the module docstring."""

    async def _sync_email_schedule(
        self,
        surface: AgentSurfaceEntity,
        *,
        previous_surface: AgentSurfaceEntity | None,
        ctx: Context | None = None,
    ) -> AgentSurfaceEntity:
        # Only Composio-trigger email surfaces (Gmail/Outlook) get a polling
        # schedule. Resend is an email surface but receives over a native webhook,
        # so it has no schedule.
        if (
            not self._is_email_surface(surface)
            or surface.event_mode is not SurfaceEventMode.COMPOSIO_TRIGGER
        ):
            if previous_surface is not None:
                await self._delete_email_schedule_if_needed(previous_surface)
            return surface

        schedule_service = self.schedule_service
        if schedule_service is None or self.connector_trigger_repository is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require schedule service dependencies"
            )

        account = await self._email_schedule_account(surface)
        reuse_id, stale_id = self._schedule_plan(surface, previous_surface)
        if stale_id is not None:
            await schedule_service.delete_schedule(stale_id)

        if reuse_id is None:
            surface.schedule_id = await self._create_email_schedule(
                surface, account, schedule_service=schedule_service, ctx=ctx
            )
        else:
            await schedule_service.update_schedule(
                reuse_id,
                ScheduleUpdateEntity(is_active=surface.is_active),
                ctx=ctx,
            )
        surface.surface_identity_email = account.email
        return await self.surface_repository.update(surface)

    async def _email_schedule_account(
        self, surface: AgentSurfaceEntity
    ) -> SurfaceAccountInfo:
        """The Composio account this surface polls, once it is fit to poll with."""
        if surface.account_id is None:
            raise AgentSurfaceValidationError("Email surfaces require account_id")
        account = await self._get_connected_account(surface.account_id)
        if surface.surface_type is SurfacePlatform.GMAIL and not account.email:
            # Gmail polling filters out the surface's own messages by email
            # (the query below); Outlook routes by account_id and works without it.
            raise AgentSurfaceValidationError(
                "Connected account must expose an email address for Gmail surfaces"
            )
        await self._ensure_composio_email_account(account)
        return account

    def _schedule_plan(
        self,
        surface: AgentSurfaceEntity,
        previous_surface: AgentSurfaceEntity | None,
    ) -> tuple[UUID | None, UUID | None]:
        """Which schedule to keep, and which to delete first.

        A surface that changed connected account cannot keep its schedule: the
        schedule carries the account it polls, so the old one goes and a new one
        takes its place.
        """
        if previous_surface is None or not self._is_email_surface(previous_surface):
            return surface.schedule_id, None
        if (
            previous_surface.schedule_id
            and previous_surface.account_id != surface.account_id
        ):
            return None, previous_surface.schedule_id
        return surface.schedule_id, None

    async def _create_email_schedule(
        self,
        surface: AgentSurfaceEntity,
        account: SurfaceAccountInfo,
        *,
        schedule_service: ScheduleService,
        ctx: Context | None,
    ) -> UUID:
        """Create the polling schedule that feeds this email surface."""
        connector_trigger_id = await self._resolve_email_connector_trigger_id(
            surface.surface_type
        )
        schedule_config: dict[str, Any] = {
            "source": "agent_surfaces_email",
            "surface_id": str(surface.id),
            "platform": surface.surface_type.value.lower(),
        }
        if surface.surface_type is SurfacePlatform.GMAIL:
            schedule_config.update(
                {
                    "userId": "me",
                    "interval": 2,
                    "labelIds": "INBOX",
                    "query": f"label:inbox -from:{account.email}",
                }
            )
        created_schedule = await schedule_service.create_schedule(
            ScheduleCreateEntity(
                user_id=account.user_id,
                pod_id=surface.pod_id,
                name=(
                    f"agent_surface_{surface.surface_type.value.lower()}_"
                    f"{str(surface.id).replace('-', '')[:8]}"
                ),
                schedule_type=ScheduleType.WEBHOOK,
                account_id=account.id,
                connector_trigger_id=connector_trigger_id,
                config=schedule_config,
            ),
            ctx=ctx,
        )
        return created_schedule.id

    async def _delete_email_schedule_if_needed(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        if not self._is_email_surface(surface):
            return
        if self.schedule_service is None:
            return
        schedule_id = surface.schedule_id
        if schedule_id is None:
            return
        await self.schedule_service.delete_schedule(schedule_id)

    async def _ensure_composio_email_account(
        self,
        account: SurfaceAccountInfo,
    ) -> None:
        if self._auth_config_port is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require Composio auth config validation"
            )
        if account.auth_config_id is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require a Composio-backed connected account"
            )
        auth_config = await self._auth_config_port.get_auth_config(
            account.auth_config_id
        )
        if auth_config is None or auth_config.kind != ConnectorKind.COMPOSIO.value:
            raise AgentSurfaceValidationError(
                "Email surfaces require a Composio-backed connected account"
            )

    async def _resolve_email_connector_trigger_id(
        self, surface_type: SurfacePlatform
    ) -> str:
        if self.connector_trigger_repository is None:
            raise AgentSurfaceValidationError(
                "Connector trigger repository is not configured"
            )
        trigger_event_name = _EMAIL_TRIGGER_EVENT_TYPES.get(
            surface_type.value.upper(), ()
        )
        triggers = (
            await self.connector_trigger_repository.get_by_app_name_and_event_type(
                surface_type.value.lower(),
                trigger_event_name,
            )
        )
        if triggers:
            return triggers[0].id
        raise AgentSurfaceValidationError(
            f"Could not find a connector trigger for {surface_type.value.lower()} email surfaces"
        )
