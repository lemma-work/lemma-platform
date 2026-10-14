"""A Slack grant arrived: finish what it was for.

Grants happen on Slack's page, and their callback lands in the connectors
module, which says only that an account was connected. Two things wait on one:

* a teammate's own app the manager made from inside Slack, awaiting its Allow --
  make the teammate's surface from the new account;
* Lemma's own app, authorised for a workspace no organization holds yet -- adopt
  the workspace, and let the manager say hello.

Either way nobody has to come back to Lemma to finish.
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
        # Here, not at the top: it reaches into identity's onboarding, which the
        # worker's every other subscriber would otherwise pay to import.
        from app.modules.agent_surfaces.services.slack_install import adopt_workspace

        async with uow_factory() as uow:
            if await bind_allowed_app(
                uow,
                auth_config_id=auth_config_id,
                account_id=account_id,
                user_id=parsed.user_id,
            ):
                return
            # Not a teammate's app awaiting its Allow: perhaps Lemma's own,
            # authorised for a workspace no organization holds yet.
            await adopt_workspace(
                uow,
                user_id=parsed.user_id,
                organization_id=parsed.organization_id,
                account_id=account_id,
            )

    await inbox.process(
        "agent-surfaces.slack-manager", event, process, max_attempts=CONSUMER_ATTEMPTS
    )
