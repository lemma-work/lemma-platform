"""Open a modal on the fresh button trigger; email and provisioning run later."""

from __future__ import annotations

import asyncio
from uuid import UUID
from pydantic import JsonValue, TypeAdapter
from slack_sdk.errors import SlackApiError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack.client import build_slack_client
from app.modules.agent_surfaces.services.onboarding_inputs import (
    OPEN_ACTION,
    require_input,
    section,
    slack_modal,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
)
from app.modules.connectors.contracts.surfaces import account_with_secrets
from app.modules.pod.contracts.agent_access import live_pod_organization_id

logger = get_logger(__name__)


async def open_onboarding_modal(
    payload: dict[str, JsonValue],
    receiver_ids: list[UUID] | None,
    uows: UnitOfWorkFactory,
) -> bool:
    try:
        async with asyncio.timeout(2.5):
            return await _open_onboarding_modal(payload, receiver_ids, uows)
    except TimeoutError:
        # Include database and credential lookup in Slack's trigger deadline.
        # The existing private prompt remains usable through typed replies.
        logger.warning("agent_surfaces.onboarding.modal_deadline_exceeded")
        return True


async def _open_onboarding_modal(
    payload: dict[str, JsonValue],
    receiver_ids: list[UUID] | None,
    uows: UnitOfWorkFactory,
) -> bool:
    actions = payload.get("actions")
    if payload.get("type") != "block_actions" or not isinstance(actions, list):
        return False
    action = next(
        (
            item
            for item in actions
            if isinstance(item, dict) and item.get("action_id") == OPEN_ACTION
        ),
        None,
    )
    if action is None:
        return False
    token, trigger = action.get("value"), payload.get("trigger_id")
    actor, tenant = (
        section(payload, "user").get("id"),
        section(payload, "team").get("id"),
    )
    if (
        not isinstance(token, str)
        or not isinstance(trigger, str)
        or not isinstance(actor, str)
        or not isinstance(tenant, str)
    ):
        raise PrivateDeliveryUnavailable("Incomplete private setup interaction")
    bound = await require_input(
        uows,
        token=token,
        platform=SurfacePlatform.SLACK,
        actor=actor,
        tenant=tenant,
        receiver_ids=receiver_ids,
    )
    if section(payload, "channel").get("id") != bound.destination.reply_target.get(
        "channel"
    ):
        raise PrivateDeliveryUnavailable("Open setup from its private conversation")
    credentials = await _installation_credentials(uows, bound.installation_id, tenant)
    client = await build_slack_client(credentials)
    try:
        async with asyncio.timeout(2):
            await client.views_open(
                trigger_id=trigger,
                view=slack_modal(
                    token, bound.step, prefill=bound.destination.sender_email or ""
                ),
            )
    except (SlackApiError, TimeoutError) as error:
        # The private prompt already offers typed replies. Do not retry an
        # expired trigger, or send setup material back to the channel.
        logger.warning(
            "agent_surfaces.onboarding.modal_unavailable",
            installation_id=str(bound.installation_id),
            error_type=type(error).__name__,
        )
    return True


async def _installation_credentials(
    uows: UnitOfWorkFactory, installation_id: UUID | None, tenant: str
) -> dict[str, JsonValue]:
    async with uows() as uow:
        surface = (
            await SurfaceRepository(uow).get(installation_id)
            if installation_id
            else None
        )
        if (
            surface is None
            or not surface.is_active
            or not surface.status.accepts_inbound_events()
            or not surface.matches_tenant(tenant)
            or surface.account_id is None
        ):
            raise PrivateDeliveryUnavailable("The Slack installation is unavailable")
        found = await account_with_secrets(uow, surface.account_id)
        organization_id = await live_pod_organization_id(uow, surface.pod_id)
        if (
            found is None
            or found[0].status != "CONNECTED"
            or found[0].organization_id != organization_id
        ):
            raise PrivateDeliveryUnavailable("The Slack installation is unavailable")
        credentials = found[1]
    return TypeAdapter(dict[str, JsonValue]).validate_python(credentials)
