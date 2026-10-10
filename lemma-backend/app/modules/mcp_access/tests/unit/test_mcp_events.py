"""Subscriptions' terms, and what a delivery does with each answer it gets."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.modules.mcp_access.domain.events import (
    MAX_PAYLOAD_BYTES,
    MAX_TTL,
    MIN_TTL,
    canonical_arguments,
    granted_ttl,
    subscription_id,
    valid_secret,
)
from app.modules.mcp_access.events.event_deliveries import (
    MAX_DEFERRALS,
    deliver_within_rate,
)
from app.modules.mcp_access.infrastructure.webhook_sender import SendResult
from app.modules.mcp_access.services.event_delivery import (
    DeliveryJob,
    EventDelivery,
    Outcome,
    occurrence_body,
    outcome_of,
)
from app.modules.mcp_access.services.event_subscriptions import (
    _verification_failure,
)


def test_a_subscription_is_never_granted_forever_or_longer_than_asked() -> None:
    assert granted_ttl(None) == timedelta(hours=1)
    assert granted_ttl(10**12) == MAX_TTL
    assert granted_ttl(1_000) == MIN_TTL
    assert granted_ttl(7_200_000) == timedelta(hours=2)


def test_the_same_arguments_in_another_order_are_the_same_subscription() -> None:
    _, one = canonical_arguments({"table": "leads", "x": 1})
    _, two = canonical_arguments({"x": 1, "table": "leads"})
    assert one == two
    grant = uuid4()
    assert subscription_id(grant, "https://a", "record.created", one) == (
        subscription_id(grant, "https://a", "record.created", two)
    )
    assert subscription_id(uuid4(), "https://a", "record.created", one) != (
        subscription_id(grant, "https://a", "record.created", one)
    )


def test_only_a_whsec_secret_of_24_to_64_bytes_is_taken() -> None:
    def secret(size: int) -> str:
        return "whsec_" + base64.b64encode(b"k" * size).decode()

    assert valid_secret(secret(32))
    assert not valid_secret(secret(16))
    assert not valid_secret(secret(65))
    assert not valid_secret("sk_live_x")
    assert not valid_secret(None)


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (SendResult(status=200, body=b'{"challenge":"c1"}'), None),
        (SendResult(status=200, body=b'{"challenge":"nope"}'), "challenge_failed"),
        (SendResult(status=200, body=b"not json"), "challenge_failed"),
        (SendResult(status=404), "http_4xx"),
        (SendResult(status=503), "http_5xx"),
        (SendResult(status=None, failure="timeout"), "timeout"),
        (SendResult(status=None, failure="connection_refused"), "connection_refused"),
    ],
)
def test_a_callback_verifies_only_by_echoing_the_challenge(result, reason) -> None:
    assert _verification_failure(result, "c1") == reason


def _job() -> DeliveryJob:
    return DeliveryJob(
        subscription_id="sub_x",
        pod_id=uuid4(),
        table="leads",
        record_id="r1",
        event_id="evt_1",
        occurred_at="2026-10-06T12:00:00Z",
    )


def test_a_row_too_large_for_the_draft_is_sent_as_its_id() -> None:
    body = occurrence_body(_job(), {"blob": "x" * (MAX_PAYLOAD_BYTES + 1)})
    assert len(body) <= MAX_PAYLOAD_BYTES
    assert json.loads(body)["data"]["record"] == {"id": "r1"}


class _Ledger:
    """The database half of a delivery, stated rather than read."""

    def __init__(self, wanted) -> None:
        self.wanted = wanted
        self.recorded: list[int | None] = []

    async def still_wanted(self, job):
        return self.wanted

    async def record(self, subscription, result) -> None:
        self.recorded.append(result.status)


def _delivery(*, wanted, record, result) -> tuple[EventDelivery, _Ledger, list[int]]:
    sent: list[int] = []

    async def read(*_: object):
        return record

    async def send(**_: object) -> SendResult:
        sent.append(1)
        return result

    ledger = _Ledger(wanted)
    return EventDelivery(ledger, read_record=read, send=send), ledger, sent


class _Subscription:
    public_id = "sub_x"
    user_id = uuid4()
    url = "https://receiver.example.com/hook"
    secret_ciphertext = "whsec_" + base64.b64encode(b"k" * 32).decode()
    refresh_before = datetime.now(timezone.utc) + timedelta(hours=1)


def test_an_unsafe_url_is_not_retried_into() -> None:
    assert outcome_of(SendResult(status=None, failure="unsafe_url:private")) is (
        Outcome.DROPPED
    )
    assert outcome_of(SendResult(status=None, failure="timeout")) is Outcome.RETRY


@pytest.mark.asyncio
async def test_an_ended_subscription_sends_nothing() -> None:
    delivery, _, sent = _delivery(
        wanted=None, record={"a": 1}, result=SendResult(status=200)
    )
    assert await delivery.deliver(_job()) is Outcome.DROPPED
    assert sent == []


@pytest.mark.asyncio
async def test_a_row_the_person_cannot_read_is_never_sent() -> None:
    delivery, _, sent = _delivery(
        wanted=_Subscription(), record=None, result=SendResult(status=200)
    )
    assert await delivery.deliver(_job()) is Outcome.DROPPED
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (204, Outcome.SENT),
        (410, Outcome.DROPPED),
        (413, Outcome.DROPPED),
        (502, Outcome.RETRY),
    ],
)
async def test_what_the_receiver_answers_decides_what_happens_next(
    status, outcome
) -> None:
    delivery, ledger, sent = _delivery(
        wanted=_Subscription(), record={"a": 1}, result=SendResult(status=status)
    )
    assert await delivery.deliver(_job()) is outcome
    assert sent == [1]
    assert ledger.recorded == [status]


@pytest.mark.asyncio
async def test_the_secret_reaches_the_signer_masked_and_revealable() -> None:
    seen: list[object] = []

    async def read(*_: object):
        return {"a": 1}

    async def send(*, secret, **_: object) -> SendResult:
        seen.append(secret)
        # What the real sender does with it.
        secret.get_secret_value().encode()
        return SendResult(status=200)

    delivery = EventDelivery(_Ledger(_Subscription()), read_record=read, send=send)
    assert await delivery.deliver(_job()) is Outcome.SENT
    [secret] = seen
    assert isinstance(secret, SecretStr)
    assert secret.get_secret_value() not in repr(secret)


class _Limiter:
    def __init__(self, wait: int | None) -> None:
        self.wait = wait
        self.keys: list[str] = []

    async def retry_after(self, key: str, *, limit: int, window_seconds: int):
        self.keys.append(key)
        return self.wait


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, object]] = []

    async def enqueue(self, job_name: str, **kwargs: object) -> object:
        self.jobs.append({"name": job_name, **kwargs})
        return None


@pytest.mark.asyncio
async def test_under_the_rate_a_delivery_goes_now() -> None:
    delivery, _, sent = _delivery(
        wanted=_Subscription(), record={"a": 1}, result=SendResult(status=200)
    )
    queue = _Queue()
    outcome = await deliver_within_rate(
        _job(),
        deferrals=0,
        delivery=delivery,
        limiter=_Limiter(None),
        queue=queue,
        now=datetime.now(timezone.utc),
    )
    assert outcome is Outcome.SENT
    assert sent == [1]
    assert queue.jobs == []


@pytest.mark.asyncio
async def test_past_the_rate_a_delivery_waits_for_the_next_window() -> None:
    delivery, _, sent = _delivery(
        wanted=_Subscription(), record={"a": 1}, result=SendResult(status=200)
    )
    queue, now, job = _Queue(), datetime.now(timezone.utc), _job()
    outcome = await deliver_within_rate(
        job,
        deferrals=2,
        delivery=delivery,
        limiter=_Limiter(17),
        queue=queue,
        now=now,
    )
    assert outcome is None, "waiting is not an outcome, and not a spent try"
    assert sent == []
    [later] = queue.jobs
    assert later["_defer_until"] == now + timedelta(seconds=17)
    assert later["deferrals"] == 3
    assert later["event_id"] == job.event_id
    assert later["_job_id"] != f"mcp-event:{job.subscription_id}:{job.event_id}"


@pytest.mark.asyncio
async def test_a_delivery_that_has_waited_long_enough_is_let_go() -> None:
    delivery, _, sent = _delivery(
        wanted=_Subscription(), record={"a": 1}, result=SendResult(status=200)
    )
    queue = _Queue()
    outcome = await deliver_within_rate(
        _job(),
        deferrals=MAX_DEFERRALS,
        delivery=delivery,
        limiter=_Limiter(5),
        queue=queue,
        now=datetime.now(timezone.utc),
    )
    assert outcome is Outcome.DROPPED
    assert sent == [] and queue.jobs == []
