"""Schedule module dependencies."""

from functools import lru_cache
from typing import Annotated
from uuid import UUID
from fastapi import Depends, Request

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_service import ScheduleService
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.domain.interfaces import WebhookVerifier
from app.modules.schedule.domain.webhook_source import WebhookSourceRegistry


def get_schedule_service(uow: UoWDep) -> ScheduleService:
    """Provide schedule service."""
    return ScheduleService(uow=uow)


def get_webhook_handler(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> WebhookHandler:
    """Provide the webhook handler in factory mode.

    Factory rather than a live ``UoWDep``: the handler holds a connection only
    for its schedule lookup, and the Redis enqueue plus outbox write that follow
    run with nothing checked out. On an inbound webhook the request rate belongs
    to the sender, so this is where a held connection hurts most.
    """

    def _matcher(uow) -> WebhookScheduleMatcher:
        return WebhookScheduleMatcher(schedule_repository=ScheduleRepository(uow=uow))

    return WebhookHandler(matcher_factory=_matcher, uow_factory=uow_factory)


def get_composio_webhook_verifier() -> WebhookVerifier:
    """Provide Composio webhook verifier."""
    from app.composition.schedule_connectors import ComposioWebhookVerifier

    return ComposioWebhookVerifier()


@lru_cache(maxsize=1)
def get_webhook_source_registry() -> WebhookSourceRegistry:
    """The sources this deployment accepts on `POST /webhooks/{source}`.

    Cached: the registry is a lookup table of stateless plugins, and rebuilding
    it per delivery would import the composition root on a path whose rate an
    external sender chooses.
    """
    from app.composition.webhook_sources import default_webhook_sources

    return default_webhook_sources()


def get_current_user_id(request: Request) -> UUID:
    """Get current user ID from request state."""
    # Assuming verify_auth middleware/dependency has run
    return request.state.user.id


ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]
WebhookHandlerDep = Annotated[WebhookHandler, Depends(get_webhook_handler)]
ComposioWebhookVerifierDep = Annotated[
    WebhookVerifier, Depends(get_composio_webhook_verifier)
]
WebhookSourceRegistryDep = Annotated[
    WebhookSourceRegistry, Depends(get_webhook_source_registry)
]
