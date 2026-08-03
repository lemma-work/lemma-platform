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
from app.core.log.log import get_logger
from app.modules.identity.contracts import IdentityEmailPort
from app.modules.identity.domain.events import (
    IDENTITY_EVENTS_STREAM,
    UserSignedUpEvent,
)
from app.composition.pod_identity_wiring import (
    create_identity_email_port,
    create_organization_repository,
    create_user_repository,
)
from app.modules.pod.domain.events import PodEvents, PodJoinRequestedEvent
from app.modules.pod.domain.pod_entities import PodRole
from app.modules.pod.domain.visibility import roles_allow_required
from app.modules.pod.services.resource_access_invite_service import (
    ResourceAccessInviteService,
)
from app.modules.pod.infrastructure.pod_repositories import (
    PodMemberRepository,
    PodRepository,
)

router = RedisRouter()
logger = get_logger(__name__)


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

    async with uow_factory() as uow:
        pod_repository = PodRepository(uow)
        pod_member_repository = PodMemberRepository(uow)
        user_repository = create_user_repository(uow)
        organization_repository = create_organization_repository(uow)

        pod = await pod_repository.get(parsed.pod_id)
        if not pod:
            logger.debug(
                'pod.pod_handlers.pod_not_found_skipping_notification.diagnostic',
                pod_id=parsed.pod_id,
            )
            return

        requester = await user_repository.get(parsed.requester_user_id)
        if not requester:
            logger.debug(
                'pod.pod_handlers.requester_not_found_skipping_notification.diagnostic'
            )
            return
        requester_name = (
            " ".join(
                part for part in [requester.first_name, requester.last_name] if part
            )
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
            if member.user_email and roles_allow_required(member.roles, PodRole.ADMIN)
        ]

    if not admin_emails:
        logger.debug(
            "pod.pod_handlers.no_pod_admins_notify_pod.observed", pod_id=parsed.pod_id
        )
        return

    for admin_email in admin_emails:
        await email_port.send_pod_join_request_email(
            to_email=admin_email,
            pod_name=pod.name,
            organization_name=organization_name,
            requester_name=requester_name,
            requester_email=str(requester.email),
        )


@reliable_redis_stream_subscriber(
    router,
    IDENTITY_EVENTS_STREAM,
    group="pod-resource-invite-redemption",
    consumer="pod-resource-invite-redemption-consumer",
)
async def on_user_signed_up_redeem_resource_invites(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Turn invites owed to a new account's address into real grants.

    A resource grant keys on a user id, so anything shared with someone before
    they had an account has been waiting as an invite. This is the moment the id
    exists, which makes it the moment the grant can be written.

    Note *when* this fires: ``UserSignedUpEvent`` is emitted on the first
    verified-email transition, not on claiming an address. That matters — an
    invite is addressed to a person, and redeeming on signup alone would let
    anyone collect someone else's by registering their address first.
    """
    if event.get("event_type") != UserSignedUpEvent.get_event_type():
        return

    async def process() -> None:
        parsed = UserSignedUpEvent.model_validate(event)
        async with uow_factory() as uow:
            redeemed = await ResourceAccessInviteService(uow.session).redeem_for_user(
                user_id=parsed.user_id,
                email=parsed.email,
            )
        if redeemed:
            logger.info(
                "pod.pod_handlers.resource_invites_redeemed.observed",
                user_id=parsed.user_id,
                count=redeemed,
            )

    await inbox.process("pod.resource-invite-redemption", event, process)
