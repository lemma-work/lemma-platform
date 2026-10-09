"""Listening to a connected MCP server: what it offers, when to renew, and
what its deliveries must prove before anything is believed about them."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.webhooks.signatures import standard_webhook_signature
from app.modules.connectors.domain.mcp_events import (
    arguments_problem,
    parse_descriptors,
    refresh_before_from,
    renew_at,
    retry_at,
)
from app.modules.connectors.infrastructure.repositories.mcp_event_repository import (
    StoredEventSubscription,
)
from app.modules.connectors.infrastructure.webhook_sources.mcp import McpWebhookSource
from app.modules.schedule.contracts import WebhookDelivery, WebhookNotVerified


def _digest(event_id: str) -> str:
    return hashlib.sha256(event_id.encode()).hexdigest()


SECRET = "whsec_" + base64.b64encode(os.urandom(32)).decode()
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def test_descriptors_keep_webhook_events_and_skip_what_is_unusable() -> None:
    found = parse_descriptors(
        {
            "events": [
                {
                    "name": "issue.created",
                    "description": "An issue was opened.",
                    "delivery": ["webhook", "push"],
                    "inputSchema": {"type": "object"},
                },
                {"name": "push.only", "delivery": ["push"]},
                {"description": "no name"},
                "not an object",
                {"name": "issue.created", "description": "a duplicate"},
            ]
        }
    )
    assert [event.name for event in found] == ["issue.created"]
    assert found[0].description == "An issue was opened."
    assert parse_descriptors({"events": "nope"}) == []
    assert parse_descriptors(None) == []


def test_arguments_are_checked_for_what_is_certain() -> None:
    schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
        "additionalProperties": False,
    }
    assert arguments_problem(schema, {"project": "web"}) is None
    assert "project" in (arguments_problem(schema, {}) or "")
    assert "colour" in (
        arguments_problem(schema, {"project": "web", "colour": 1}) or ""
    )
    assert arguments_problem({}, {"anything": 1}) is None


def test_an_argument_of_the_wrong_kind_is_named_before_the_server_refuses_it() -> None:
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "urgent": {"type": "boolean"},
            "state": {"type": "string", "enum": ["open", "closed"]},
            "label": {"type": ["string", "null"]},
            "free": {},
        },
    }
    fine = {"limit": 5, "urgent": True, "state": "open", "label": None, "free": [1]}
    assert arguments_problem(schema, fine) is None
    assert "limit" in (arguments_problem(schema, {"limit": "5"}) or "")
    assert "limit" in (arguments_problem(schema, {"limit": True}) or ""), (
        "a boolean is not a JSON integer, whatever Python thinks"
    )
    assert "urgent" in (arguments_problem(schema, {"urgent": "true"}) or "")
    assert "state" in (arguments_problem(schema, {"state": "merged"}) or "")


def test_a_subscription_is_renewed_halfway_or_when_the_next_pass_is_too_late() -> None:
    lapses = NOW + timedelta(hours=24)
    assert renew_at(granted_at=NOW, refresh_before=lapses) == NOW + timedelta(hours=12)
    short = NOW + timedelta(minutes=8)
    assert renew_at(granted_at=NOW, refresh_before=short) <= NOW


def test_a_failing_renewal_waits_longer_each_time_up_to_a_ceiling() -> None:
    lapsed = NOW - timedelta(hours=1)
    waits = [
        retry_at(failures=failures, refresh_before=lapsed, now=NOW) - NOW
        for failures in (1, 2, 3, 8, 50)
    ]
    assert waits == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(hours=6),
        timedelta(hours=6),
    ]


def test_a_failing_renewal_still_gets_its_last_chance_before_lapsing() -> None:
    lapses = NOW + timedelta(hours=1)
    assert retry_at(failures=6, refresh_before=lapses, now=NOW) == NOW + timedelta(
        minutes=50
    )
    assert retry_at(failures=1, refresh_before=lapses, now=NOW) == NOW + timedelta(
        minutes=5
    )


def test_refresh_before_falls_back_when_the_server_names_none() -> None:
    assert refresh_before_from("2026-10-07T12:00:00Z", now=NOW) == NOW + timedelta(
        hours=24
    )
    assert refresh_before_from(None, now=NOW) == NOW + timedelta(hours=1)
    assert refresh_before_from("not a time", now=NOW) == NOW + timedelta(hours=1)


def _stored(subscription_id: UUID, remote_id: str | None) -> StoredEventSubscription:
    return StoredEventSubscription(
        id=subscription_id,
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        account_id=uuid4(),
        user_id=uuid4(),
        name="issue.created",
        arguments={},
        secret_ciphertext="",
        remote_id=remote_id,
        granted_at=None,
        refresh_before=None,
    )


def _source(subscription_id: UUID, remote_id: str | None = "sub_remote"):
    heard: list[UUID] = []

    async def listening(asked: UUID):
        if asked != subscription_id:
            return None
        return _stored(subscription_id, remote_id), SecretStr(SECRET)

    async def note(asked: UUID) -> None:
        heard.append(asked)

    return McpWebhookSource(listening=listening, heard=note), heard


def _delivery(
    subscription_id: UUID,
    body: dict[str, object],
    *,
    secret: str = SECRET,
    remote_id: str = "sub_remote",
    signed_at: int | None = None,
) -> WebhookDelivery:
    raw = json.dumps(body).encode()
    timestamp = signed_at if signed_at is not None else int(time.time())
    return WebhookDelivery(
        source="mcp",
        raw_body=raw,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": str(timestamp),
            "webhook-signature": standard_webhook_signature(
                secret, "msg_1", timestamp, raw
            ),
            "X-MCP-Subscription-Id": remote_id,
        },
        query={"subscription": str(subscription_id)},
    )


@pytest.mark.asyncio
async def test_the_challenge_is_echoed_and_starts_nothing() -> None:
    subscription_id = uuid4()
    source, _ = _source(subscription_id, remote_id=None)
    verified = await source.verify(
        _delivery(
            subscription_id,
            {"type": "verification", "challenge": "abc123"},
            remote_id="not yet known",
        )
    )
    assert verified.reply == {"challenge": "abc123"}


@pytest.mark.asyncio
async def test_an_occurrence_routes_by_our_id_and_dedupes_on_its_event_id() -> None:
    subscription_id = uuid4()
    source, heard = _source(subscription_id)
    verified = await source.verify(
        _delivery(
            subscription_id,
            {
                "eventId": "evt_1",
                "name": "issue.created",
                "timestamp": "2026-10-06T12:00:00Z",
                "data": {"title": "Login is broken"},
                "cursor": None,
            },
        )
    )
    assert verified.reply is None
    await source.observe(verified)
    assert heard == [subscription_id]
    normalized = source.normalize(verified)
    assert normalized is not None
    assert normalized.match == {"provider_trigger_id": str(subscription_id)}
    assert normalized.source_event_id == f"mcp:{subscription_id}:" + _digest("evt_1")
    assert normalized.account_id is not None
    assert normalized.account_id == verified.account_id, "only that account's schedules"
    assert normalized.payload["data"] == {"title": "Login is broken"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["wrong_secret", "unknown_subscription", "other_remote_id", "stale", "no_query"],
)
async def test_a_delivery_that_proves_nothing_is_refused(tamper: str) -> None:
    subscription_id = uuid4()
    source, _ = _source(subscription_id)
    body = {"eventId": "evt_1", "name": "issue.created", "data": {}}
    other = "whsec_" + base64.b64encode(os.urandom(32)).decode()
    delivery = {
        "wrong_secret": lambda: _delivery(subscription_id, body, secret=other),
        "unknown_subscription": lambda: _delivery(uuid4(), body),
        "other_remote_id": lambda: _delivery(subscription_id, body, remote_id="sub_x"),
        "stale": lambda: _delivery(
            subscription_id, body, signed_at=int(time.time()) - 3600
        ),
        "no_query": lambda: WebhookDelivery(
            source="mcp", raw_body=b"{}", headers={}, query={}
        ),
    }[tamper]()
    with pytest.raises(WebhookNotVerified):
        await source.verify(delivery)


@pytest.mark.asyncio
async def test_stream_notices_are_acknowledged_and_run_nothing() -> None:
    subscription_id = uuid4()
    source, _ = _source(subscription_id)
    for body in (
        {"type": "terminated", "reason": "revoked"},
        {"type": "gap", "eventId": "evt_9", "name": "issue.created"},
        {"name": "issue.created"},
    ):
        verified = await source.verify(_delivery(subscription_id, body))
        assert source.normalize(verified) is None
