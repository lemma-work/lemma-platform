"""Telling a person they are in a pod, or that they are not, as delivery work.

Two letters, both to the person whose access changed and both written in the
transaction that changed it, keyed so each is sent once:

* the answer to their request to join, approved or declined. Before this the
  asker was told nothing either way: an approval sat unnoticed until they
  happened to reload, and a refusal was not possible at all;
* being added by hand. An accepted invitation already said yes and needs no
  letter, and joining through an open door was the person's own click, so only
  somebody else adding them is announced.
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
from app.modules.pod.domain.events import (
    PodJoinRequestDecidedEvent,
    PodMemberAddedEvent,
)

logger = get_logger(__name__)

POD_JOIN_DECIDED_EMAIL = "pod.join_request.decided_email"
POD_MEMBER_ADDED_EMAIL = "pod.member.added_email"


class JoinDecidedEmailPayload(BaseModel):
    join_request_id: UUID
    pod_id: UUID
    organization_id: UUID
    requester_user_id: UUID
    decided_by_user_id: UUID
    approved: bool


class MemberAddedEmailPayload(BaseModel):
    pod_id: UUID
    user_id: UUID
    added_by_user_id: UUID


class _Letter(BaseModel):
    to_email: str
    pod_name: str
    organization_name: str
    by_name: str


def _email_port() -> IdentityEmailPort:
    from app.modules.identity.contracts.organizations import (
        build_identity_email_sender,
    )

    return build_identity_email_sender()


def _name_of(user) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part) or str(
        user.email
    )


async def _read_letter(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, to_user_id: UUID, by_user_id: UUID
) -> _Letter | None:
    from app.modules.identity.contracts.organizations import (
        build_organization_membership,
        build_user_directory,
    )
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    pod = await PodRepository(uow).get(pod_id)
    if pod is None:
        return None
    users = build_user_directory(uow)
    to_user = await users.get(to_user_id)
    if to_user is None:
        return None
    by_user = await users.get(by_user_id)
    organization = await build_organization_membership(uow).get(pod.organization_id)
    return _Letter(
        to_email=str(to_user.email),
        pod_name=pod.name,
        organization_name=organization.name if organization else "",
        by_name=_name_of(by_user) if by_user else "",
    )


async def run_join_decided_email(
    ctx: JobContext, payload: JoinDecidedEmailPayload
) -> None:
    async with ctx.checkpoint() as uow:
        letter = await _read_letter(
            uow,
            pod_id=payload.pod_id,
            to_user_id=payload.requester_user_id,
            by_user_id=payload.decided_by_user_id,
        )
    if letter is None:
        logger.info(
            "pod.access_email.skipped",
            pod_id=str(payload.pod_id),
            reason="pod_or_person_gone",
        )
        return
    await _email_port().send_pod_join_decided_email(
        to_email=letter.to_email,
        pod_id=payload.pod_id,
        pod_name=letter.pod_name,
        organization_name=letter.organization_name,
        approved=payload.approved,
        decided_by_name=letter.by_name,
    )


async def run_member_added_email(
    ctx: JobContext, payload: MemberAddedEmailPayload
) -> None:
    async with ctx.checkpoint() as uow:
        letter = await _read_letter(
            uow,
            pod_id=payload.pod_id,
            to_user_id=payload.user_id,
            by_user_id=payload.added_by_user_id,
        )
    if letter is None:
        logger.info(
            "pod.access_email.skipped",
            pod_id=str(payload.pod_id),
            reason="pod_or_person_gone",
        )
        return
    await _email_port().send_pod_member_added_email(
        to_email=letter.to_email,
        pod_id=payload.pod_id,
        pod_name=letter.pod_name,
        organization_name=letter.organization_name,
        added_by_name=letter.by_name,
    )


def register_access_email_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=POD_JOIN_DECIDED_EMAIL,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=JoinDecidedEmailPayload,
    )(run_join_decided_email)
    registry.handler(
        kind=POD_MEMBER_ADDED_EMAIL,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=MemberAddedEmailPayload,
    )(run_member_added_email)


register_access_email_work(default_registry)


async def add_join_decided_email_work(
    uow: SqlAlchemyUnitOfWork, event: PodJoinRequestDecidedEvent
) -> None:
    await WorkWriter.of(uow).add(
        POD_JOIN_DECIDED_EMAIL,
        pod_namespace(event.pod_id),
        JoinDecidedEmailPayload(
            join_request_id=event.join_request_id,
            pod_id=event.pod_id,
            organization_id=event.organization_id,
            requester_user_id=event.requester_user_id,
            decided_by_user_id=event.decided_by_user_id,
            approved=event.approved,
        ),
        dedup_key=f"pod-join-decided:{event.join_request_id}",
    )


async def add_member_added_email_work(
    uow: SqlAlchemyUnitOfWork, event: PodMemberAddedEvent
) -> None:
    """Only for somebody else adding them; see the module's note."""
    if event.added_by_user_id is None or event.added_by_user_id == event.user_id:
        return
    await WorkWriter.of(uow).add(
        POD_MEMBER_ADDED_EMAIL,
        pod_namespace(event.pod_id),
        MemberAddedEmailPayload(
            pod_id=event.pod_id,
            user_id=event.user_id,
            added_by_user_id=event.added_by_user_id,
        ),
        dedup_key=f"pod-member-added:{event.pod_id}:{event.user_id}:{event.event_id}",
    )
