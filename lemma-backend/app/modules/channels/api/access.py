"""Who may do what with a pod's channels, for every channel route.

**Seeing** a channel is one rule (``domain.access.may_see``), asked once per
request through a ``Viewer`` built here: a member's own DIRECT channel; a
contact's channel for who holds ``contact.read``; a public group for every
member; a private group for its members. A channel the reader may not see
answers exactly as one that does not exist -- 404 -- since whether it exists is
not theirs to learn. Lists, counts, the inbox and search filter by the same
rule in SQL.

On top of seeing it, one table -- the module doc (``docs/modules/channels.md``)
repeats it. ``channel.manage`` implies ``channel.write``, which implies
``channel.read``; ``contact.message`` implies ``contact.read``. By role:
viewers read, members (``POD_USER``) write, editors and admins manage.

=====================================  ========================================
Reading channels, lines, members,      ``channel.read``
threads, deliveries, inbox, search
One's own channel (``/channels/me``);  ``channel.read``
joining a public group or a CONTACT
channel; leaving; one's own notify;
marking read
Answering a question                   ``channel.read``; only the person it
                                       waits on
Writing, editing, deleting one's       ``channel.write`` (+ ``contact.message``
lines; starting a group; retrying      in a CONTACT channel); a push to
one's own line's delivery (or any,     oneself is retried by its recipient
with ``may_manage``)                   alone, with ``channel.write``
Group: rename, topic, visibility,      ``channel.write`` and the channel's
archive, delete, members, roles,       owner -- or ``channel.manage``
agents roster; deleting anybody's      (``may_manage``)
line (shown as removed by them)
CONTACT channel: assignee, status,     ``channel.write`` + ``contact.message``
agent switch, hand-back window
Seeing a CONTACT channel at all        ``contact.read``
A group source's settings              seeing the channel (else 404) or
(``/channels/{c}/sources/{s}``)        ``channel.manage``, and the bot's
                                       ``agent.read``; changing them
                                       ``agent.update`` and its owner, or a pod
                                       admin (``agent_surfaces``)
=====================================  ========================================
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status

from app.core.api.dependencies import CurrentUser
from app.core.api.schemas import ErrorResponse
from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import Channel, MemberRole
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository


class Caller:
    """The member making this request: who they are to the visibility rule,
    and the pod-wide permissions channel routes ask about."""

    def __init__(
        self,
        viewer: Viewer,
        *,
        messages_contacts: bool,
        manages_channels: bool,
        writes_channels: bool,
    ) -> None:
        self.viewer = viewer
        self.messages_contacts = messages_contacts
        self.manages_channels = manages_channels
        self.writes_channels = writes_channels

    @property
    def user_id(self) -> UUID:
        return self.viewer.user_id

    @property
    def pod_id(self) -> UUID:
        return self.viewer.pod_id


async def caller_from(ctx: Context, *, pod_id: UUID, user_id: UUID) -> Caller:
    """The member ``user_id`` in ``pod_id``, as every channel read asks it --
    the REST routes and the live socket alike, so the two cannot drift."""
    pod = ResourceRef.pod(pod_id)
    return Caller(
        Viewer(
            user_id=user_id,
            pod_id=pod_id,
            sees_contacts=await ctx.can(Permissions.CONTACT_READ, pod),
        ),
        messages_contacts=await ctx.can(Permissions.CONTACT_MESSAGE, pod),
        manages_channels=await ctx.can(Permissions.CHANNEL_MANAGE, pod),
        writes_channels=await ctx.can(Permissions.CHANNEL_WRITE, pod),
    )


async def get_caller(pod_id: UUID, user: CurrentUser, ctx: PodContextDep) -> Caller:
    return await caller_from(ctx, pod_id=pod_id, user_id=user.id)


CallerDep = Annotated[Caller, Depends(get_caller)]


# -- refusals ----------------------------------------------------------------------
#
# Each carries a code a client can match on (the error envelope's ``code``),
# not only a status: ``CHANNEL_NOT_FOUND``, ``WRONG_CHANNEL_KIND``,
# ``LAST_OWNER`` ... A 422 is ``VALIDATION_ERROR``, as a request the schema
# refused is.


def refusal(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def refusal_message(error: HTTPException) -> str:
    """What a refusal says, without its code."""
    detail = error.detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    return str(detail)


def missing(what: str = "Channel") -> HTTPException:
    return refusal(
        status.HTTP_404_NOT_FOUND,
        f"{what.upper().replace(' ', '_')}_NOT_FOUND",
        f"{what} not found",
    )


def conflict(detail: str, *, code: str = "CHANNEL_CONFLICT") -> HTTPException:
    return refusal(status.HTTP_409_CONFLICT, code, detail)


def wrong_kind(detail: str) -> HTTPException:
    """Asked of a channel of the wrong kind: a member's own, a contact's, a
    group's."""
    return conflict(detail, code="WRONG_CHANNEL_KIND")


def forbidden(detail: str) -> HTTPException:
    return refusal(status.HTTP_403_FORBIDDEN, "INSUFFICIENT_PERMISSION", detail)


def unprocessable(detail: str) -> HTTPException:
    return refusal(status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", detail)


#: What a channel route may answer besides its success: the shared error
#: envelope (``ErrorResponse``) under each status, for the OpenAPI document.
ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "A cursor this API did not give."},
    403: {
        "model": ErrorResponse,
        "description": "Not allowed: `INSUFFICIENT_PERMISSION`.",
    },
    404: {
        "model": ErrorResponse,
        "description": (
            "Not there, or not the reader's to see: `CHANNEL_NOT_FOUND`, "
            "`LINE_NOT_FOUND`, `MEMBER_NOT_FOUND` ..."
        ),
    },
    409: {
        "model": ErrorResponse,
        "description": (
            "Not in this channel's state: `CHANNEL_ARCHIVED`, "
            "`WRONG_CHANNEL_KIND`, `LAST_OWNER`, `LAST_MEMBER`, "
            "`NOT_JOINED`, `CHANNEL_CONFLICT`."
        ),
    },
    422: {
        "model": ErrorResponse,
        "description": "The request cannot be done as asked: `VALIDATION_ERROR`.",
    },
}


async def visible(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> Channel:
    """The channel, if it is in the pod and the caller may see it; else 404."""
    channel = await ChannelRepository(uow.session).in_pod(
        pod_id=caller.pod_id, channel_id=channel_id
    )
    if channel is None:
        raise missing()
    is_member = (
        await MemberRepository(uow.session).of_user(channel.id, caller.user_id)
        is not None
    )
    if not may_see(channel, caller.viewer, is_member=is_member):
        raise missing()
    return channel


async def may_manage(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> bool:
    """Whether the caller may change this channel's settings and roster: its
    owner while they may write in the pod (``channel.write``) -- an owner made
    a viewer keeps the seat, not the say -- or whoever manages the pod's
    channels (``channel.manage``)."""
    if caller.manages_channels:
        return True
    if not caller.writes_channels:
        return False
    seat = await MemberRepository(uow.session).of_user(channel.id, caller.user_id)
    return seat is not None and seat.role is MemberRole.OWNER


async def require_manage(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> None:
    if not await may_manage(uow, caller, channel):
        raise forbidden(
            "Only this channel's owners, or an editor of the pod, can change it"
        )


def require_contact_message(caller: Caller) -> None:
    if not caller.messages_contacts:
        raise forbidden("Writing to contacts is for the pod's editors and admins")


# -- cursors -----------------------------------------------------------------------


def encode_cursor(at: datetime, row_id: UUID) -> str:
    raw = f"{at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    """A cursor this API handed out, or a 400."""
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, row_id = raw.partition("|")
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:  # bad base64, text or fields alike
        raise refusal(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_CURSOR",
            "That cursor is not one we gave",
        ) from None
