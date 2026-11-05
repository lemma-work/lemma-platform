"""The pod live socket's frames: what a client may send and what it is sent.

One socket per (client, pod) carries many subscriptions, each to one *topic*
that a module provides (``topics.py``), and calls -- small ephemeral actions
answered on the same socket. The client names each subscription and each call
with its own ``id`` and the server answers on that id::

    -> {"op":"subscribe","id":"s1","topic":"channel","channel_id":"…","since":"…"}
    -> {"op":"unsubscribe","id":"s1"}
    -> {"op":"call","id":"c1","method":"typing","params":{"channel_id":"…"}}
    -> {"op":"ping"}
    <- {"type":"ready","connection":"…","topics":[…],"calls":[…]}
    <- {"type":"subscribed","id":"s1","cursor":"…"}
    <- {"type":"events","id":"s1","cursor":"…","events":[{"type":"line.created",…}]}
    <- {"type":"changes","id":"s1","cursor":"…","changes":[…]}   (topic records)
    <- {"type":"resync","id":"s1","cursor":"…","reason":"…"}
    <- {"type":"unsubscribed","id":"s1","reason":"…"}
    <- {"type":"result","id":"c1","result":{…}}
    <- {"type":"error","id":"s1|c1","code":"…","message":"…"}
    <- {"type":"pong"}

A subscribe frame's other fields are the topic's parameters; the topic checks
them. ``topic`` defaults to ``records``, which is what the socket carried before
it carried anything else. Everything here is pure; the connection and the loop
that runs a socket are in ``connection.py`` and ``socket.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic import ValidationError

#: A JSON object as a frame carries it.
JsonObject = dict[str, JsonValue]

#: The topic a subscribe frame without one asks for.
DEFAULT_TOPIC = "records"

#: Largest client frame the server reads. Subscribe and call frames are small.
MAX_CLIENT_FRAME_BYTES = 16 * 1024

# -- close codes ----------------------------------------------------------------

#: The session is missing, invalid or expired: refresh the token and reconnect.
#: Same code as the browser-view socket and every SDK's live client.
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
#: The user already holds as many sockets on this pod as they may.
CLOSE_TOO_MANY_SOCKETS = 4429


class ResyncReason(StrEnum):
    #: ``since`` is older than the topic can replay from, or is not a cursor.
    CURSOR_EXPIRED = "cursor_expired"
    #: More changes for this subscription in one batch than are worth sending.
    TOO_MANY = "too_many"
    #: What the subscription covers changed under it.
    PERMISSION_CHANGED = "permission_changed"
    #: The socket could not keep up; its queued frames were dropped.
    OVERFLOW = "overflow"
    #: The topic keeps no history: a resumed subscription refetches.
    NO_REPLAY = "no_replay"


class UnsubscribedReason(StrEnum):
    ACCESS_REVOKED = "access_revoked"
    #: What it watched is gone: a dropped table, a deleted channel.
    GONE = "gone"
    CLIENT = "client"


class LiveErrorCode(StrEnum):
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    LIMIT = "limit"
    #: The server could not do it just now; try again later.
    UNAVAILABLE = "unavailable"


class LiveRequestInvalid(ValueError):
    """An inbound frame the server cannot act on; answered, never fatal."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class LiveAccessDenied(Exception):
    """A subscription or call the caller may not make; answered with ``code``."""

    def __init__(self, code: LiveErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LiveUnavailable(Exception):
    """What serves a topic did not start, was stopped, or took too long. The
    socket says so and the client retries."""


# -- inbound ----------------------------------------------------------------------

_RequestId = Annotated[str, Field(min_length=1, max_length=64)]
_Name = Annotated[str, Field(min_length=1, max_length=64)]


class _Subscribe(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    op: Literal["subscribe"]
    id: _RequestId
    topic: _Name = DEFAULT_TOPIC
    since: Annotated[str, Field(min_length=1, max_length=512)] | None = None


class UnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    op: Literal["unsubscribe"]
    id: _RequestId


class PingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    op: Literal["ping"]


class CallRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    op: Literal["call"]
    id: _RequestId
    method: _Name
    params: JsonObject = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    """One subscribe frame: the fields every topic shares, and the rest."""

    id: str
    topic: str = DEFAULT_TOPIC
    since: str | None = None
    #: Every other field of the frame, for the topic to check.
    params: JsonObject = field(default_factory=dict)


_Inbound = Annotated[
    _Subscribe | UnsubscribeRequest | PingRequest | CallRequest,
    Field(discriminator="op"),
]
_INBOUND = TypeAdapter(_Inbound)

LiveRequest = SubscribeRequest | UnsubscribeRequest | PingRequest | CallRequest


def _first_error(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {"msg": "invalid frame"}
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first['msg']}" if location else str(first["msg"])


def parse_request(raw: str) -> LiveRequest:
    """Parse one client frame, or raise ``LiveRequestInvalid`` saying why."""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiveRequestInvalid("Frame is not JSON.") from exc
    request_id = decoded.get("id") if isinstance(decoded, dict) else None
    try:
        parsed = _INBOUND.validate_python(decoded)
    except ValidationError as exc:
        raise LiveRequestInvalid(
            _first_error(exc),
            request_id=request_id if isinstance(request_id, str) else None,
        ) from exc
    if isinstance(parsed, _Subscribe):
        return SubscribeRequest(
            id=parsed.id,
            topic=parsed.topic,
            since=parsed.since,
            params=dict(parsed.model_extra or {}),
        )
    return parsed


def parse_params[M: BaseModel](
    model: type[M], params: JsonObject, *, request_id: str
) -> M:
    """A topic's or call's parameters as ``model``; ``LiveRequestInvalid`` if not."""
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise LiveRequestInvalid(_first_error(exc), request_id=request_id) from exc


# -- outbound ----------------------------------------------------------------------


def dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def ready_frame(connection_id: str, *, topics: list[str], calls: list[str]) -> str:
    return dumps(
        {
            "type": "ready",
            "connection": connection_id,
            "topics": topics,
            "calls": calls,
        }
    )


def subscribed_frame(subscription_id: str, cursor: str | None) -> str:
    return dumps(
        {
            "type": "subscribed",
            "id": subscription_id,
            "cursor": cursor,
            "stream_id": cursor,
        }
    )


def events_frame(subscription_id: str, cursor: str | None, events: list[str]) -> str:
    """A batch of a topic's events, each already encoded as JSON."""
    return (
        '{"type":"events","id":'
        + dumps(subscription_id)
        + ',"cursor":'
        + dumps(cursor)
        + ',"events":['
        + ",".join(events)
        + "]}"
    )


def resync_frame(subscription_id: str, cursor: str | None, reason: ResyncReason) -> str:
    return dumps(
        {
            "type": "resync",
            "id": subscription_id,
            "cursor": cursor,
            "stream_id": cursor,
            "reason": reason.value,
        }
    )


def error_frame(request_id: str | None, code: LiveErrorCode, message: str) -> str:
    return dumps(
        {"type": "error", "id": request_id, "code": code.value, "message": message}
    )


def unsubscribed_frame(subscription_id: str, reason: str) -> str:
    return dumps({"type": "unsubscribed", "id": subscription_id, "reason": reason})


def result_frame(call_id: str, result: JsonValue) -> str:
    return dumps({"type": "result", "id": call_id, "result": result})


PONG_FRAME = '{"type":"pong"}'


__all__ = [
    "CLOSE_FORBIDDEN",
    "CLOSE_NOT_FOUND",
    "CLOSE_TOO_MANY_SOCKETS",
    "CLOSE_UNAUTHENTICATED",
    "DEFAULT_TOPIC",
    "MAX_CLIENT_FRAME_BYTES",
    "PONG_FRAME",
    "CallRequest",
    "JsonObject",
    "LiveAccessDenied",
    "LiveErrorCode",
    "LiveRequest",
    "LiveRequestInvalid",
    "LiveUnavailable",
    "PingRequest",
    "ResyncReason",
    "SubscribeRequest",
    "UnsubscribeRequest",
    "UnsubscribedReason",
    "dumps",
    "error_frame",
    "events_frame",
    "parse_params",
    "parse_request",
    "ready_frame",
    "result_frame",
    "resync_frame",
    "subscribed_frame",
    "unsubscribed_frame",
]
