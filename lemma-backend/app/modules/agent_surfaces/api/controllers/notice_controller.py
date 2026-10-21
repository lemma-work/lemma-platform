"""A member telling another member something: a line in their own channel.

A member cannot write in another member's own channel, so this is how a page
in Lemma -- a doc comment that names somebody, say -- tells them: a line from
the pod's agent, said on the writer's behalf, in the recipient's channel. It
is pushed to them like any line of theirs (``services.channel_deliveries``).

A member sends at most ``NOTICES_PER_MINUTE`` a minute in a pod: a page that
loops, or a script, cannot flood a colleague's channel and phone.
"""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, status
from pydantic import AfterValidator, BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
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
