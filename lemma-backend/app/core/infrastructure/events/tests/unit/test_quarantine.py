"""A message that cannot be processed must be given up on, and only that one.

The two halves matter equally. Quarantining too little is the bug this exists
for: two malformed events on ``surface_events`` were redelivered more than 680
times each and would have kept going forever. Quarantining too much silently
drops work that a retry would have completed — a Redis failover during a deploy
must not turn into lost events.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.core.infrastructure.events import quarantine as q

pytestmark = pytest.mark.unit


class _Event(BaseModel):
    source: str
    payload: dict


def _validation_error() -> ValidationError:
    """A real pydantic failure, the same shape the poison messages produced."""
    try:
        _Event.model_validate({"event_type": "surface.connected", "pod_id": "x"})
    except ValidationError as error:
        return error
    raise AssertionError("expected a ValidationError")


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.counters: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def xadd(self, stream, entry, maxlen=None, approximate=True):
        self.streams.setdefault(stream, []).append(entry)

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key, seconds):
        self.expiries[key] = seconds


class _Message:
    def __init__(
        self,
        *,
        stream="surface_events",
        group="surface-webhook-events",
        message_id="1786862052832-0",
        body=b'{"event_type":"surface.connected"}',
    ):
        self.raw_message = {"channel": stream, "group": group}
        self.message_id = message_id
        self.body = body


@pytest.fixture
def redis(monkeypatch) -> _FakeRedis:
    client = _FakeRedis()
    monkeypatch.setattr(q, "get_redis", lambda **kwargs: client)
    return client


def _middleware() -> q.StreamQuarantineMiddleware:
    """FastStream builds these per message, with the consume context attached."""
    return q.StreamQuarantineMiddleware(None, context=None)


async def _consume(middleware, msg, error: Exception | None):
    async def call_next(_):
        if error is not None:
            raise error
        return "handled"

    return await middleware.consume_scope(call_next, msg)


# -- classification ---------------------------------------------------------


def test_a_validation_failure_is_permanent():
    assert q.is_permanent(_validation_error())


def test_a_wrapped_validation_failure_is_permanent():
    """fast-depends raises its own error with the pydantic failure as the cause."""
    wrapper = RuntimeError("solving handler signature failed")
    wrapper.__cause__ = _validation_error()

    assert q.is_permanent(wrapper)


def test_malformed_json_is_permanent():
    assert q.is_permanent(json.JSONDecodeError("bad", "{", 0))


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("redis went away"),
        TimeoutError("database restart"),
        RuntimeError("something we have not seen"),
    ],
    ids=["connection", "timeout", "unknown"],
)
def test_everything_else_is_treated_as_transient(error):
    """The permanent set is closed: an unrecognised error still gets its retries."""
    assert not q.is_permanent(error)


# -- behaviour --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_healthy_message_passes_straight_through(redis):
    result = await _consume(_middleware(), _Message(), None)

    assert result == "handled"
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_a_permanent_failure_is_dead_lettered_and_swallowed(redis):
    """Swallowing is the point: it is what lets the ack clear the PEL entry."""
    result = await _consume(_middleware(), _Message(), _validation_error())

    assert result is None
    entries = redis.streams["surface_events:dead"]
    assert len(entries) == 1
    assert entries[0]["original_stream"] == "surface_events"
    # Sourced from the subscriber registry, which a bare unit run has not
    # populated; the e2e asserts the real value.
    assert "consumer_groups" in entries[0]
    assert entries[0]["message_id"] == "1786862052832-0"
    assert entries[0]["error_type"] == "ValidationError"
    # The body rides along, or the dead letter cannot be diagnosed or replayed.
    assert "surface.connected" in entries[0]["body"]


@pytest.mark.asyncio
async def test_a_transient_failure_is_re_raised_and_not_dead_lettered(redis):
    with pytest.raises(ConnectionError):
        await _consume(_middleware(), _Message(), ConnectionError("redis"))

    assert redis.streams == {}


@pytest.mark.asyncio
async def test_a_transient_failure_that_never_stops_is_eventually_given_up_on(redis):
    """The backstop. A 'transient' error that is really permanent still ends."""
    middleware = _middleware()
    msg = _Message()

    for _ in range(q.MAX_DELIVERY_ATTEMPTS - 1):
        with pytest.raises(ConnectionError):
            await _consume(middleware, msg, ConnectionError("still down"))
    assert redis.streams == {}

    result = await _consume(middleware, msg, ConnectionError("still down"))

    assert result is None
    assert len(redis.streams["surface_events:dead"]) == 1


@pytest.mark.asyncio
async def test_the_backstop_counts_each_message_separately(redis):
    """One noisy message must not spend another message's retry budget."""
    middleware = _middleware()

    for index in range(q.MAX_DELIVERY_ATTEMPTS):
        with pytest.raises(ConnectionError):
            await _consume(
                middleware, _Message(message_id=f"{index}-0"), ConnectionError("x")
            )

    assert redis.streams == {}


@pytest.mark.asyncio
async def test_a_dead_letter_write_failure_still_acks(monkeypatch):
    """Re-raising here would restore the very loop this exists to break."""

    class _BrokenRedis(_FakeRedis):
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(q, "get_redis", lambda **kwargs: _BrokenRedis())

    result = await _consume(_middleware(), _Message(), _validation_error())

    assert result is None


def test_dead_letter_stream_is_derived_from_the_source():
    assert q.dead_letter_stream("surface_events") == "surface_events:dead"


def test_message_details_survive_a_message_that_says_nothing():
    """This runs on the failure path; it must not raise on an odd message."""
    assert q.describe_message(object()) == ("", "")
