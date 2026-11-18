"""A member telling another member something: a line in their own channel.

A member cannot write in another member's own channel, so this is how a page
in Lemma -- a doc comment that names somebody, say -- tells them: a line from
the pod's agent, said on the writer's behalf, in the recipient's channel. It
is pushed to them like any line of theirs (``services.channel_deliveries``).

A member sends at most ``NOTICES_PER_MINUTE`` a minute in a pod: a page that
loops, or a script, cannot flood a colleague's channel and phone.

``/members/me/notices`` is the same line to the caller themselves: the one
way a named function or agent tells the person it runs as something -- a
morning note, a brief the evening before. It takes ``channel.notify_self``,
which such a workload can be granted on the pod, where ``channel.write`` (to
anybody) cannot. It has no recipient to name: the line goes to whoever the
request acts for, so a grant of it reaches that person and nobody else.
"""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, status
from pydantic import AfterValidator, BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.actor import WorkloadKind
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.function_run import NOBODY_USER_ID
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.agent_surfaces.composition import build_direct_messages
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.services.direct_messages import DirectMessage
from app.modules.channels.contracts import keyed_client_id

logger = get_logger(__name__)

router = APIRouter(prefix="/pods/{pod_id}/members", tags=["Agent Surfaces"])

#: How many notices one member may send in a pod each minute.
NOTICES_PER_MINUTE = 20


def _no_nul(value: str) -> str:
    """Stored as Postgres text, which cannot hold a NUL character."""
    if "\x00" in value:
        raise ValueError("Text cannot contain a NUL character")
    return value


class NoticeRequest(BaseModel):
    text: Annotated[str, AfterValidator(_no_nul)] = Field(min_length=1, max_length=4000)


class NoticeResponse(BaseModel):
    message_id: UUID
    channel_id: UUID


# Before `/{member}/notices`, which would otherwise take "me" as a member to
# look up -- and ask for `channel.write`, which a workload cannot hold.
@router.post(
    "/me/notices",
    operation_id="agent.notice.send_self",
    response_model=NoticeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CHANNEL_NOTIFY_SELF)],
    responses={
        403: {"description": "The request acts for nobody in this pod."},
        404: {"description": "The person it acts for is not in this pod."},
        429: {"description": "Too many notices this minute; see Retry-After."},
    },
)
async def send_self_notice(
    pod_id: UUID,
    body: NoticeRequest,
    ctx: PodContextDep,
    uow: UoWDep,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> NoticeResponse:
    """Tell the person this request acts for something, in their own channel:
    a function or agent writing to the person it runs as, or a member writing
    a note to themselves. There is no recipient to name. A workload needs
    `channel.notify_self` granted on the pod (`resource_type` `pod`,
    `resource_name` `/`); a member holds it with `channel.write`. Idempotency
    and the per-minute cap are the same as for a notice to another member."""
    person = ctx.user_id
    if person is None or person == NOBODY_USER_ID:
        # A run for a contact or an anonymous form has nobody to tell; its
        # token never reaches here (`function_run.allows_path`), and this says
        # so again rather than trusting that it cannot.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only a request acting for a member of this pod has someone to tell.",
        )
    await _within_rate(pod_id, person)
    messages = build_direct_messages(uow)
    # Asked of the person's own user id, not through the "member id, user id
    # or email" resolver: there is nothing here to resolve.
    if await messages.membership.get_pod_member_id(person, pod_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="The person this acts for is not a member of this pod.",
        )
    recipient = person
    workload = ctx.workload
    try:
        line = await messages.send(
            DirectMessage(
                pod_id=pod_id,
                recipient_user_id=recipient,
                text=body.text,
                actor_user_id=person,
                # An agent says it as itself; a function as the pod's agent.
                actor_agent_id=(
                    workload.workload_id
                    if workload is not None and workload.kind is WorkloadKind.AGENT
                    else None
                ),
                key=keyed_client_id(idempotency_key, user_id=person),
            )
        )
    except AgentSurfaceValidationError as refused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.message
        ) from refused
    await uow.commit()
    return NoticeResponse(message_id=line.id, channel_id=line.channel_id)


@router.post(
    "/{member}/notices",
    operation_id="agent.notice.send",
    response_model=NoticeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
    responses={
        404: {"description": "Nobody in this pod is that member."},
        429: {"description": "Too many notices this minute; see Retry-After."},
    },
)
async def send_notice(
    pod_id: UUID,
    member: Annotated[
        str,
        Path(
            min_length=1,
            max_length=320,
            description=(
                "The member to tell: a pod member id, user id, or email address."
            ),
        ),
    ],
    body: NoticeRequest,
    user: CurrentUser,
    uow: UoWDep,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=255
    ),
) -> NoticeResponse:
    """Tell another member of the pod something, in their own channel. The
    same `Idempotency-Key` from the same member is the same notice; sent with
    other text, it is refused (422). A member sends at most
    `NOTICES_PER_MINUTE` a minute (429)."""
    await _within_rate(pod_id, user.id)
    messages = build_direct_messages(uow)
    recipient = await messages.membership.resolve_pod_recipient(
        pod_id=pod_id, reference=member
    )
    if recipient is None:
        # 404, not 422: from the caller's side, nobody in this pod and somebody
        # outside it are the same fact.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No pod member matches that."
        )
    try:
        line = await messages.send(
            DirectMessage(
                pod_id=pod_id,
                recipient_user_id=recipient,
                text=body.text,
                actor_user_id=user.id,
                key=keyed_client_id(idempotency_key, user_id=user.id),
            )
        )
    except AgentSurfaceValidationError as refused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.message
        ) from refused
    await uow.commit()
    return NoticeResponse(message_id=line.id, channel_id=line.channel_id)


async def _within_rate(pod_id: UUID, user_id: UUID) -> None:
    """Count one notice from this member this minute; 429 past the limit. A
    count Redis cannot give lets the notice through: the cap is a guard
    against floods, not a promise to anybody."""
    minute = int(time.time() // 60)
    try:
        sent = await incr_with_ttl(
            get_redis(), f"notices:rate:{pod_id}:{user_id}:{minute}", 120
        )
    except Exception:  # noqa: BLE001 -- best effort; logged with its traceback
        logger.warning(
            "agent_surfaces.notice.rate_count_unavailable.degraded",
            pod_id=str(pod_id),
            exc_info=True,
        )
        return
    if sent > NOTICES_PER_MINUTE:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "TOO_MANY_NOTICES",
                "message": (
                    f"At most {NOTICES_PER_MINUTE} notices a minute; try again shortly"
                ),
            },
            headers={"Retry-After": str(max(1, (minute + 1) * 60 - int(time.time())))},
        )
