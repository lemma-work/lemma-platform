"""A teammate's Slack app, allowed: make its surface from the new account.

The manager makes a teammate's app from inside Slack and hands the person
Slack's Allow. Allowing happens on Slack's page, and its callback lands in the
connectors module, which says only that an account was connected. This listens
for that and finishes the job, so the teammate is in Slack without anybody
coming back to Lemma.
"""

from __future__ import annotations

from uuid import UUID

from faststream import Depends

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.agent_surfaces.domain.delivery_limits import CONSUMER_ATTEMPTS
from app.modules.agent_surfaces.events.handlers import provide_uow_factory, router
from app.modules.agent_surfaces.services.slack_manager_bots import bind_allowed_app
from app.modules.connectors.domain.events import (
    CONNECTOR_EVENTS_STREAM,
    ConnectorConnectedEvent,
)


@reliable_redis_stream_subscriber(
    router,
    CONNECTOR_EVENTS_STREAM,
    group="surface-slack-manager",
    consumer="surface-slack-manager-consumer",
)
async def on_connector_connected(
    event: dict[str, object],
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != ConnectorConnectedEvent.get_event_type():
        return
    parsed = ConnectorConnectedEvent.model_validate(event)
    if (
        parsed.connector_id != "slack"
        or not parsed.auth_config_id
        or not parsed.account_id
    ):
        return
    auth_config_id: UUID = parsed.auth_config_id
    account_id: UUID = parsed.account_id

    async def process() -> None:
        async with uow_factory() as uow:
            await bind_allowed_app(
                uow,
                auth_config_id=auth_config_id,
                account_id=account_id,
                user_id=parsed.user_id,
            )

    await inbox.process(
        "agent-surfaces.slack-manager", event, process, max_attempts=CONSUMER_ATTEMPTS
    )
