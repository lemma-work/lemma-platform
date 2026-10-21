"""What channels put on the pod's live socket.

* ``channel`` -- one channel's timeline, for a reader who may see it:
  ``{"topic":"channel","channel_id":"…","since":"<cursor>"}``.
* ``inbox`` -- the reader's counts across the pod: ``{"topic":"inbox"}``.
* ``typing`` -- the caller is writing in a channel (or one of its threads);
  its other readers hear.
* ``read`` -- the caller has seen a channel up to a line: ``POST …/read``,
  over the socket.

Who may watch is decided exactly as the REST routes decide it: the same
``Caller`` (``readers.caller_for``), the same ``visible`` check, the same
permission (``channel.read``; ``channel.write`` to type, and
``contact.message`` to type to a contact).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.config import live_settings
from app.core.live.connection import LiveConnection
from app.core.live.limits import admit_user_call
from app.core.live.protocol import (
    JsonObject,
    LiveAccessDenied,
    LiveErrorCode,
    ResyncReason,
    SubscribeRequest,
    parse_params,
)
from app.core.live.signals import publish_signal
from app.core.live.topics import LiveContribution
from app.modules.channels.api.schemas import ref
from app.modules.channels.domain.entities import ChannelKind, Principal
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.services.live.readers import require_caller, visible_channel
from app.modules.channels.services.reading import NotInChannel, read_to

if TYPE_CHECKING:
    from app.modules.channels.services.live.hub import ChannelsHub


def _uow():
    return SessionUnitOfWorkFactory(async_session_maker)()


def _hub(pod_id: UUID) -> ChannelsHub:
    # Imported on the first subscription: an API process that never serves
    # one does not load the hub at boot.
    from app.modules.channels.services.live.hubs import channels_hubs

    return channels_hubs().hub(pod_id)


class _ChannelParams(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    channel_id: UUID


#: The largest line number a channel stores (``BIGINT``).
_MAX_SEQ = 2**63 - 1


class _ReadParams(_ChannelParams):
    #: Bounded as stored: a number past it is the caller's mistake, answered
    #: ``invalid``, not a failed write answered ``unavailable`` and retried.
    seq: int = Field(ge=0, le=_MAX_SEQ)
    #: Read in this thread, not the timeline (``services.reading``).
    thread_root_id: UUID | None = None


class _TypingParams(_ChannelParams):
    #: Typing in a thread: its root line. Relayed as given -- the typist may
    #: see the channel, and it only says where their composer is.
    thread_root_id: UUID | None = None


class ChannelTopic:
    name = "channel"

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        params = parse_params(_ChannelParams, request.params, request_id=request.id)
        async with _uow() as uow:
            caller = await require_caller(
                uow, pod_id=connection.pod_id, user_id=connection.user_id
            )
        # Whether they may see it is decided in the hub, under its lock.
        await _hub(connection.pod_id).join_channel(
            connection,
            request.id,
            caller=caller,
            channel_id=params.channel_id,
            since=request.since,
        )


class InboxTopic:
    name = "inbox"

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        async with _uow() as uow:
            caller = await require_caller(
                uow, pod_id=connection.pod_id, user_id=connection.user_id
            )
        await _hub(connection.pod_id).join_inbox(connection, request.id, caller=caller)
        if request.since is not None:
            # Counts are not a log: a resumed reader refetches `/inbox`.
            connection.offer_resync(request.id, None, ResyncReason.NO_REPLAY)


class TypingCall:
    """``typing {channel_id}``: tell the channel's other readers, now. Limited
    per socket (core) and per user across their sockets (here)."""

    name = "typing"

    def per_minute(self) -> int:
        return live_settings.live_typing_calls_per_minute

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        parsed = parse_params(_TypingParams, params, request_id=call_id)
        per_user = live_settings.live_typing_calls_per_user_per_minute
        if not await admit_user_call(
            connection.pod_id, connection.user_id, "typing", per_user
        ):
            raise LiveAccessDenied(
                LiveErrorCode.LIMIT,
                f"At most {per_user} typing calls a minute across your sockets.",
            )
        async with _uow() as uow:
            caller = await require_caller(
                uow,
                pod_id=connection.pod_id,
                user_id=connection.user_id,
                needs=Permissions.CHANNEL_WRITE,
            )
            channel = await visible_channel(uow, caller, parsed.channel_id)
            if channel.kind is ChannelKind.CONTACT and not caller.messages_contacts:
                raise LiveAccessDenied(
                    LiveErrorCode.FORBIDDEN,
                    "Writing to contacts is for the pod's editors and admins",
                )
            typist = Principal.member(connection.user_id)
            names = await Names(uow).of(pod_id=connection.pod_id, people=[typist])
        publish_signal(
            connection.pod_id,
            {
                "k": "typing",
                "c": str(channel.id),
                "u": str(connection.user_id),
                "who": ref(typist, names).model_dump(mode="json"),
                "t": str(parsed.thread_root_id) if parsed.thread_root_id else None,
            },
        )
        return {}


class ReadCall:
    """``read {channel_id, seq, thread_root_id?}``: ``POST
    /channels/{c}/read``, the same rule and the same write. The reader's
    other sockets hear ``read.updated``."""

    name = "read"

    def per_minute(self) -> int:
        return live_settings.live_read_calls_per_minute

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        parsed = parse_params(_ReadParams, params, request_id=call_id)
        async with _uow() as uow:
            caller = await require_caller(
                uow, pod_id=connection.pod_id, user_id=connection.user_id
            )
            channel = await visible_channel(uow, caller, parsed.channel_id)
            try:
                read_seq = await read_to(
                    uow,
                    channel,
                    user_id=caller.user_id,
                    seq=parsed.seq,
                    thread_root_id=parsed.thread_root_id,
                )
            except NotInChannel:
                raise LiveAccessDenied(
                    LiveErrorCode.NOT_FOUND, "Line not found"
                ) from None
            await uow.commit()
        return {"channel_id": str(channel.id), "read_seq": read_seq}


def live_contribution() -> LiveContribution:
    return LiveContribution(
        topics=(ChannelTopic(), InboxTopic()), calls=(TypingCall(), ReadCall())
    )


__all__ = [
    "ChannelTopic",
    "InboxTopic",
    "ReadCall",
    "TypingCall",
    "live_contribution",
]
