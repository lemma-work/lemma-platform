"""Telling a pod's admins that someone asked to join, as a delivery work item.

Written in the transaction that records the request, keyed by the request, so
the email exists exactly when the request does and asking twice while the
first is still queued sends one. A ``delivery`` JOB because it sends mail: the
admins are read in a short transaction, and the sends happen after it closes.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.identity.contracts import IdentityEmailPort
from app.modules.pod.domain.events import PodJoinRequestedEvent

logger = get_logger(__name__)

POD_JOIN_REQUEST_EMAIL = "pod.join_request.email"


class JoinRequestEmailPayload(BaseModel):
    join_request_id: UUID
    pod_id: UUID
    organization_id: UUID
    requester_user_id: UUID


class _JoinRequestEmail(BaseModel):
    pod_name: str
    organization_name: str
    requester_name: str
    requester_email: str
    admin_emails: list[str]


def _email_port() -> IdentityEmailPort:
    from app.modules.identity.contracts.organizations import (
        build_identity_email_sender,
    )

    return build_identity_email_sender()


async def _read_email(
    uow: SqlAlchemyUnitOfWork, payload: JoinRequestEmailPayload
) -> _JoinRequestEmail | None:
    from app.modules.identity.contracts.organizations import (
        build_organization_membership,
        build_user_directory,
    )
    from app.modules.pod.domain.pod_entities import PodRole
    from app.modules.pod.domain.visibility import roles_allow_required
    from app.modules.pod.infrastructure.pod_repositories import (
        PodMemberRepository,
        PodRepository,
    )

    pod = await PodRepository(uow).get(payload.pod_id)
    if pod is None:
        return None
    requester = await build_user_directory(uow).get(payload.requester_user_id)
    if requester is None:
        return None
    organization = await build_organization_membership(uow).get(payload.organization_id)
    members, _ = await PodMemberRepository(uow).list_pod_members(
        payload.pod_id, limit=1000
    )
    return _JoinRequestEmail(
        pod_name=pod.name,
        organization_name=organization.name if organization else "",
        requester_name=" ".join(
            part for part in (requester.first_name, requester.last_name) if part
        ),
        requester_email=str(requester.email),
        admin_emails=[
            member.user_email
            for member in members
            if member.user_email and roles_allow_required(member.roles, PodRole.ADMIN)
        ],
    )


async def run_join_request_email(
    ctx: JobContext, payload: JoinRequestEmailPayload
) -> None:
    async with ctx.checkpoint() as uow:
        email = await _read_email(uow, payload)
    if email is None or not email.admin_emails:
        logger.info(
            "pod.join_request.email_skipped",
            pod_id=str(payload.pod_id),
            reason="no_recipient" if email is not None else "pod_or_requester_gone",
        )
        return
    port = _email_port()
    for admin_email in email.admin_emails:
        await port.send_pod_join_request_email(
            to_email=admin_email,
            pod_name=email.pod_name,
            organization_name=email.organization_name,
            requester_name=email.requester_name,
            requester_email=email.requester_email,
        )


def register_join_request_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=POD_JOIN_REQUEST_EMAIL,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=JoinRequestEmailPayload,
    )(run_join_request_email)


register_join_request_work(default_registry)


async def add_join_request_email_work(
    uow: SqlAlchemyUnitOfWork, event: PodJoinRequestedEvent
) -> None:
    await WorkWriter.of(uow).add(
        POD_JOIN_REQUEST_EMAIL,
        pod_namespace(event.pod_id),
        JoinRequestEmailPayload(
            join_request_id=event.join_request_id,
            pod_id=event.pod_id,
            organization_id=event.organization_id,
            requester_user_id=event.requester_user_id,
        ),
        dedup_key=f"pod-join-request:{event.join_request_id}",
    )
