"""Pod event handlers."""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import redis_stream_sub
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.identity.domain.ports import IdentityEmailPort
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.pod.domain.events import (
    PodCreatedEvent,
    PodEvents,
    PodJoinRequestedEvent,
)
from app.modules.pod.domain.pod_entities import PodRole
from app.modules.pod.domain.visibility import roles_allow_required
from app.modules.pod.infrastructure.pod_repositories import (
    PodMemberRepository,
    PodRepository,
)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def provide_identity_email_port() -> IdentityEmailPort:
    return SmtpIdentityEmailAdapter()


@router.subscriber(stream=redis_stream_sub(PodEvents.STREAM))
async def on_pod_created(
    event: dict,
    fs_logger: Logger,
):
    """Handle pod creation event by provisioning pod-scoped data storage.

    This is a system-level operation, so we use repositories directly
    instead of going through the service layer (which enforces user-level
    ACL checks that are not applicable here).
    """
    event_type = event.get("event_type")
    if event_type != PodCreatedEvent.get_event_type():
        return

    parsed = PodCreatedEvent.model_validate(event)
    fs_logger.info(f"Processing PodCreatedEvent for pod {parsed.pod_id}")

    schema_manager = SchemaManager()
    try:
        await schema_manager.create_datastore_schema(parsed.pod_id)
        fs_logger.info(f"Created pod data schema for pod {parsed.pod_id}")
    except Exception as e:
        fs_logger.error(f"Failed to create pod data schema: {e}")


@router.subscriber(stream=redis_stream_sub(PodEvents.STREAM))
async def on_pod_join_requested(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    email_port: IdentityEmailPort = Depends(provide_identity_email_port),
):
    """Notify pod admins by email when a user requests to join a pod."""
    if event.get("event_type") != PodJoinRequestedEvent.get_event_type():
        return

    parsed = PodJoinRequestedEvent.model_validate(event)
    fs_logger.info(
        f"Processing PodJoinRequestedEvent for pod {parsed.pod_id} "
        f"(request {parsed.join_request_id})"
    )

    async with uow_factory() as uow:
        pod_repository = PodRepository(uow)
        pod_member_repository = PodMemberRepository(uow)
        user_repository = UserRepository(uow)
        organization_repository = OrganizationRepository(uow)

        pod = await pod_repository.get(parsed.pod_id)
        if not pod:
            fs_logger.warning(f"Pod {parsed.pod_id} not found; skipping notification")
            return

        requester = await user_repository.get(parsed.requester_user_id)
        if not requester:
            fs_logger.warning(
                f"Requester {parsed.requester_user_id} not found; skipping notification"
            )
            return
        requester_name = (
            " ".join(part for part in [requester.first_name, requester.last_name] if part)
            or ""
        )

        organization = await organization_repository.get(parsed.organization_id)
        organization_name = organization.name if organization else ""

        members, _ = await pod_member_repository.list_pod_members(
            parsed.pod_id, limit=1000
        )
        admin_emails = [
            member.user_email
            for member in members
            if member.user_email
            and roles_allow_required(member.roles, PodRole.ADMIN)
        ]

    if not admin_emails:
        fs_logger.info(f"No pod admins to notify for pod {parsed.pod_id}")
        return

    for admin_email in admin_emails:
        await email_port.send_pod_join_request_email(
            to_email=admin_email,
            pod_name=pod.name,
            organization_name=organization_name,
            requester_name=requester_name,
            requester_email=str(requester.email),
        )
    fs_logger.info(
        f"Sent pod join request emails to {len(admin_emails)} admin(s) "
        f"for pod {parsed.pod_id}"
    )
