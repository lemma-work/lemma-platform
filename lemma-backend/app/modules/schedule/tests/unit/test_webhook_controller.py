import logging
from types import SimpleNamespace

import pytest

from app.modules.connectors.infrastructure.webhook_sources.composio import (
    _reshape as _normalize_composio_payload,
)
from app.modules.schedule.api.controllers.webhook_controller import (
    MAX_WEBHOOK_BODY_BYTES,
    router,
)
from app.modules.schedule.api.dependencies import (
    get_webhook_handler,
    get_webhook_source_registry,
)
from app.modules.schedule.contracts.webhook_source import (
    VerifiedDelivery,
    WebhookDelivery,
    WebhookSourceRegistry,
)
from app.modules.test_support.body_ceiling import (
    CHUNK_BYTES,
    app_with,
    chunks_of,
    chunks_to_cross,
    deliver,
    past_ceiling,
    too_large,
)


def test_direct_schedule_webhook_route_is_removed():
    """The unauthenticated, unverified POST /webhooks/schedules/{id} endpoint was
    deleted (security_authz-02). Only the composio-verified /webhooks/{source}
    routes remain."""
    paths = {route.path for route in router.routes}
    assert "/webhooks/schedules/{schedule_id}" not in paths
    assert "/webhooks/{source}" in paths


def test_normalize_composio_payload_maps_sdk_result_to_internal_shape():
    verification_result = {
        "version": "V3",
        "payload": {
            "id": "ti_123",
            "uuid": "ti_123",
            "user_id": "user_123",
            "toolkit_slug": "GOOGLECALENDAR",
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "metadata": {
                "id": "ti_123",
                "uuid": "ti_123",
                "toolkit_slug": "GOOGLECALENDAR",
                "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
                "trigger_data": None,
                "trigger_config": {},
                "connected_account": {
                    "id": "ca_123",
                    "uuid": "ca_123",
                    "auth_config_id": "ac_123",
                    "auth_config_uuid": "ac_123",
                    "user_id": "user_123",
                    "status": "ACTIVE",
                },
            },
            "payload": {
                "event_id": "evt_123",
            },
        },
        "raw_payload": {
            "id": "msg_123",
            "timestamp": "2026-03-22T06:50:57.477Z",
            "type": "composio.trigger.message",
            "metadata": {
                "log_id": "log_123",
            },
            "data": {
                "event_id": "evt_123",
            },
        },
    }

    normalized = _normalize_composio_payload(verification_result)

    assert normalized["type"] == "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER"
    assert normalized["webhook_type"] == "composio.trigger.message"
    assert normalized["data"] == {"event_id": "evt_123"}
    assert normalized["metadata"]["trigger_id"] == "ti_123"
    assert normalized["metadata"]["connected_account_id"] == "ca_123"
    assert normalized["metadata"]["version"] == "V3"


def test_normalize_composio_payload_falls_back_to_raw_data_payload():
    verification_result = {
        "version": "V3",
        "payload": {
            "id": "ti_123",
            "uuid": "ti_123",
            "user_id": "user_123",
            "toolkit_slug": "GOOGLECALENDAR",
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "metadata": {
                "connected_account": {
                    "id": "ca_123",
                    "auth_config_id": "ac_123",
                },
            },
            "payload": None,
        },
        "raw_payload": {
            "type": "composio.trigger.message",
            "data": {
                "event_id": "evt_123",
            },
        },
    }

    normalized = _normalize_composio_payload(verification_result)

    assert normalized["data"] == {"event_id": "evt_123"}
    assert normalized["metadata"]["trigger_id"] == "ti_123"


@pytest.mark.asyncio
async def test_composio_webhook_verification_does_not_run_on_the_event_loop(
    monkeypatch,
) -> None:
    """Signature verification runs the Composio SDK, which is synchronous.

    This path is unauthenticated and externally driven: the sender chooses the
    rate, and every delivery used to construct a fresh `Composio(...)` client
    (measured at 76ms cold / 4ms warm at the sibling call site in
    `composio_auth_provider`) and then run a blocking SDK call -- all of it on
    the event loop, where it stalls every other request in the process.

    Asserting on the thread rather than on elapsed time: a timing test would be
    flaky under load, while the thread identity is exactly the property. If
    someone drops the `run_blocking` the SDK lands back on the loop thread and
    this fails.
    """
    import threading

    from app.modules.connectors.config import connector_settings
    from app.modules.connectors.infrastructure import composio_triggers

    loop_thread = threading.current_thread()
    ran_on: list[threading.Thread] = []

    class _Triggers:
        def verify_webhook(self, **kwargs):
            ran_on.append(threading.current_thread())
            return {"payload": {"id": "ti_1"}}

    monkeypatch.setattr(
        composio_triggers,
        "_webhook_verification_client",
        lambda: SimpleNamespace(triggers=_Triggers()),
    )
    monkeypatch.setattr(
        connector_settings, "composio_webhook_secret", "shh", raising=False
    )

    result = await composio_triggers.verify_webhook("{}", {"webhook-id": "wh_1"})

    assert result == {"payload": {"id": "ti_1"}}
    (thread,) = ran_on
    assert thread is not loop_thread, (
        "the Composio SDK ran on the event loop thread; an unauthenticated "
        "sender can now stall every other request by sending webhooks"
    )


def test_the_published_verification_is_async_so_it_can_offload() -> None:
    """A sync `verify_webhook` could not offload, and callers cannot make it."""
    import inspect

    from app.modules.connectors.contracts.triggers import verify_webhook

    assert inspect.iscoroutinefunction(verify_webhook)


class _RecordingSource:
    """GitHub, as far as the route can tell: it records the bytes it is asked
    to verify and answers each delivery as a handshake, so nothing is matched."""

    source = "github"

    def __init__(self) -> None:
        self.verified: list[bytes] = []

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        self.verified.append(delivery.raw_body)
        return VerifiedDelivery(
            delivery=delivery, payload={}, reply={"message": "handshake"}
        )

    async def observe(self, verified: VerifiedDelivery) -> None:
        return None

    def normalize(self, verified: VerifiedDelivery) -> None:
        return None


def _app(source: _RecordingSource):
    return app_with(
        router,
        {
            get_webhook_source_registry: lambda: WebhookSourceRegistry([source]),
            get_webhook_handler: lambda: None,
        },
    )


async def test_github_is_refused_past_one_mib_before_reading_when_it_says_so():
    """GitHub allows 25 MB; this route does not, and does not read to find out."""
    source = _RecordingSource()

    refused = await deliver(
        _app(source),
        "/webhooks/github",
        [b"\0" * (MAX_WEBHOOK_BODY_BYTES + 1)],
        declared_length=MAX_WEBHOOK_BODY_BYTES + 1,
    )

    assert refused.status == 413
    assert refused.json() == too_large(MAX_WEBHOOK_BODY_BYTES)
    assert refused.chunks_read == 0
    assert source.verified == []


async def test_a_chunked_delivery_with_no_length_is_refused_as_it_crosses():
    source = _RecordingSource()

    refused = await deliver(
        _app(source), "/webhooks/github", past_ceiling(MAX_WEBHOOK_BODY_BYTES)
    )

    assert refused.status == 413
    assert refused.json() == too_large(MAX_WEBHOOK_BODY_BYTES)
    assert refused.chunks_read == chunks_to_cross(MAX_WEBHOOK_BODY_BYTES)
    assert source.verified == []


async def test_an_unknown_source_is_refused_before_its_body_is_read():
    refused = await deliver(
        _app(_RecordingSource()),
        "/webhooks/not-a-source",
        past_ceiling(MAX_WEBHOOK_BODY_BYTES),
    )

    assert refused.status == 403
    assert refused.chunks_read == 0


async def test_the_source_verifies_the_bytes_exactly_as_they_arrived():
    """At the ceiling, chunked with no length: every byte, in order, once."""
    source = _RecordingSource()
    body = bytes(range(256)) * (MAX_WEBHOOK_BODY_BYTES // 256)

    answered = await deliver(_app(source), "/webhooks/github", chunks_of(body))

    assert answered.status == 200
    assert answered.json() == {"message": "handshake"}
    assert source.verified == [body]


def _oversized_deliveries(caplog) -> list[dict[str, object]]:
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event")
        == "schedule.webhook_controller.rejecting_oversized_delivery.degraded"
    ]


@pytest.mark.parametrize(
    ("declared", "received"),
    [
        (True, 0),
        (False, chunks_to_cross(MAX_WEBHOOK_BODY_BYTES) * CHUNK_BYTES),
    ],
    ids=["declared", "chunked"],
)
async def test_a_refused_delivery_is_logged_with_its_source_and_what_arrived(
    caplog, declared, received
):
    """The request log records a 413 as a status on `/webhooks/{source}` and no
    more, so this warning is the only place a flood names its source. The size
    is what was read, never the length the sender declared."""
    chunks = past_ceiling(MAX_WEBHOOK_BODY_BYTES)

    with caplog.at_level(logging.WARNING):
        refused = await deliver(
            _app(_RecordingSource()),
            "/webhooks/github",
            chunks,
            declared_length=sum(map(len, chunks)) if declared else None,
        )

    assert refused.status == 413
    assert [
        (warning["source"], warning["size"])
        for warning in _oversized_deliveries(caplog)
    ] == [("github", received)]
