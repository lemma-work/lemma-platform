"""Pod event handlers."""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.modules.identity.contracts import IdentityEmailPort
from app.composition.pod_identity_wiring import (
    create_identity_email_port,
    create_organization_repository,
    create_user_repository,
)
from app.modules.pod.domain.events import PodEvents, PodJoinRequestedEvent
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
    return create_identity_email_port()


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="pod-join-request-events",
    consumer="pod-join-request-events-consumer",
)
async def on_pod_join_requested(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    email_port: IdentityEmailPort = Depends(provide_identity_email_port),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Notify pod admins by email when a user requests to join a pod."""
    if event.get("event_type") != PodJoinRequestedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = PodJoinRequestedEvent.model_validate(event)
        await _process_pod_join_requested(
            parsed,
            fs_logger,
            uow_factory=uow_factory,
            email_port=email_port,
        )

    await inbox.process("pod.join-request-email", event, process)


async def _process_pod_join_requested(
    parsed: PodJoinRequestedEvent,
    fs_logger: Logger,
    *,
    uow_factory: UnitOfWorkFactory,
    email_port: IdentityEmailPort,
) -> None:
    fs_logger.info(
        f"Processing PodJoinRequestedEvent for pod {parsed.pod_id} "
        f"(request {parsed.join_request_id})"
    )

    async with uow_factory() as uow:
        pod_repository = PodRepository(uow)
        pod_member_repository = PodMemberRepository(uow)
        user_repository = create_user_repository(uow)
        organization_repository = create_organization_repository(uow)

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
