"""A day of Slack webhooks through the real route, and ingest fairness across pods.

* **Classification at volume.** 10,000 signed Slack webhooks (one day at the
  10k/day rate, compressed into one run, 16 in flight) synthesised from the
  event types prod receives (``slack_webhook_mix``). Everything nobody acts on
  is answered ``200`` and dropped in the request; each actionable delivery is
  one ``surface_inbound_messages`` row and one ``surface.ingest`` item, and a
  retry of one is no second row. Request latency is measured.
* **Per-pod fairness.** A pod that receives a flood of messages does not hold
  back another pod's: each ingest item runs in its pod's namespace, and the
  dispatcher's first claim round already serves the quiet pod.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import Counter

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
)
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.work_payloads import IngestPayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_INGEST
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
    build_telegram_secret_headers,
)
from app.modules.agent_surfaces.tests.slack_webhook_mix import (
    EXPECTED,
    slack_webhook_mix,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    a_telegram_bot,
    scalar,
)

# Both tests count queued ``work_items``; a running session worker (started by
# any earlier module in the shard) claims and finishes them first -- the day of
# webhooks counted 3 queued ingests of 1,040 stored messages, and 0 of 262
# platform updates. Held, the rows stay where the request left them.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]

DAY = 10_000
IN_FLIGHT = 16


async def test_a_day_of_slack_webhooks_is_classified_in_the_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-load-e2e",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    # End the read the helper's refresh opened: the day below takes minutes,
    # and an e2e session idle in a transaction for 60s is closed under it.
    await db_session.commit()
    deliveries = slack_webhook_mix(DAY)
    expected = Counter(EXPECTED[d.kind] for d in deliveries)
    latencies: list[float] = []
    gate = asyncio.Semaphore(IN_FLIGHT)

    async def post(payload: dict) -> None:
        raw = json.dumps(payload).encode()
        headers = build_slack_signature_headers(
            raw_body=raw, signing_secret="slack-secret"
        )
        async with gate:
            started = time.perf_counter()
            response = await authenticated_client.post(
                "/surfaces/webhooks/slack", content=raw, headers=headers
            )
            latencies.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text

    started = time.perf_counter()
    # In arrival order per batch, so a retry never overtakes its original.
    for offset in range(0, DAY, 500):
        await asyncio.gather(
            *(post(d.payload) for d in deliveries[offset : offset + 500])
        )
    wall = time.perf_counter() - started

    stored = await scalar("SELECT count(*) FROM surface_inbound_messages")
    ingests = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = 'surface.ingest'"
    )
    updates = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = 'surface.platform_update'"
    )
    latencies.sort()
    p50 = statistics.median(latencies)
    p99 = latencies[int(len(latencies) * 0.99) - 1]
    dropped = DAY - stored - expected["duplicate"] - updates
    print(
        f"classification: {DAY} webhooks in {wall:.1f}s ({DAY / wall:.0f}/s); "
        f"dropped {dropped} ({dropped / DAY:.1%}), stored {stored}, "
        f"duplicates {expected['duplicate']}, platform updates {updates}; "
        f"latency p50 {p50 * 1000:.1f} ms, p99 {p99 * 1000:.1f} ms"
    )
    assert stored == expected["message"] == ingests
    assert updates == expected["platform_update"]
    assert dropped == expected["drop"]
    assert p99 < 0.3


async def test_a_flooded_pod_does_not_hold_back_another_pods_ingest(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_telegram,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": "quiet-pod",
            "description": "fairness e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    quiet_pod = response.json()["id"]
    busy, busy_secret, account = await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token="e2e-busy-token",
        surface_name="busy-bot",
    )
    quiet, quiet_secret, _ = await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=quiet_pod,
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token="e2e-quiet-token",
        surface_name="quiet-bot",
        account=account,
    )

    async def say(surface: str, secret: str, index: int) -> None:
        payload = _telegram_payload(
            text=f"m{index}", message_id=index, sender_id=7000 + index
        )
        result = await authenticated_client.post(
            f"/surfaces/{surface}/webhook",
            content=json.dumps(payload).encode(),
            headers=build_telegram_secret_headers(secret),
        )
        assert result.status_code == 200

    for index in range(200):  # the flood arrives first...
        await say(busy, busy_secret, 10_000 + index)
    for index in range(3):  # ...then three messages for the quiet pod
        await say(quiet, quiet_secret, 20_000 + index)

    namespaces = Counter(
        row
        for row in [
            await scalar(
                "SELECT string_agg(ns, ',') FROM work_items WHERE kind = 'surface.ingest'"
            )
        ][0].split(",")
    )
    assert namespaces == {f"pod:{test_pod['id']}": 200, f"pod:{quiet_pod}": 3}

    claimed: list[str] = []
    registry = WorkRegistry()

    @registry.handler(
        kind=SURFACE_INGEST,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=IngestPayload,
        concurrency=1,
    )
    async def record(ctx: JobContext, payload: IngestPayload) -> None:
        del payload
        claimed.append(ctx.item.ns)

    dispatcher = dispatcher_for(
        async_session_maker, WorkClass.INTERACTIVE, registry=registry, slots=8
    )
    first_round = await dispatcher.claim_once()
    first_namespaces = Counter(item.ns for item in first_round)
    print(f"fairness: first claim round {dict(first_namespaces)}")
    assert first_namespaces[f"pod:{quiet_pod}"] >= 1, (
        "the quiet pod waited behind the flood"
    )
    for item in first_round:
        await dispatcher.execute(item)
