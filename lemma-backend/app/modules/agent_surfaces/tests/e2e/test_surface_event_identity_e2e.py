"""Two bots, two counters: an inbound event has to be identified by its receiver.

Telegram's ``update_id`` is a per-bot counter that starts low, so every pod with
its own bot produces update 1, update 2, ... An event identity built from the
platform and that counter alone makes the second bot's first message a duplicate
of the first bot's: the durable inbox claims the row once, ``InboxConsumer``
returns False for the second, and the person gets no reply, no error and no log
line.

These drive both bots through the real webhook route, the real inbox and the
real ingress, and count the jobs that come out the far end -- one per person,
and still only one when the same update is genuinely redelivered.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.events.inbox import provide_domain_event_inbox
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.events.handlers import _process_surface_webhook
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _ensure_connector_account,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
)
from app.modules.connectors.infrastructure.models.account import Account

pytestmark = pytest.mark.e2e


class _RecordingJobQueue:
    """A job queue that records what ingress asked to be run.

    Implements ``JobQueuePort.enqueue``; the streaq worker itself is out of
    scope here, because what these tests are about is which deliveries reach a
    job at all.
    """

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        self.jobs.append((str(kwargs.get("_job_id") or ""), dict(kwargs)))

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        raise AssertionError("no test here aborts a job")


async def _a_telegram_bot(
    db_session: AsyncSession,
    authenticated_client: AsyncClient,
    *,
    pod_id: str,
    user_id: str,
    api_base: str,
    bot_token: str,
    surface_name: str,
    account: Account | None = None,
) -> tuple[str, str, Account]:
    """One Telegram surface with a bot of its own.

    Returns the surface id, its webhook secret, and the account it is bound to,
    so a second bot can be built beside the first. A second needs its own
    account row: a Telegram account answers for exactly one surface
    (``ensure_unique_telegram_account``), which is the whole premise of "every
    pod gets its own bot".
    """
    if account is None:
        connected = await _ensure_connector_account(
            db_session,
            user_id=user_id,
            connector_id="telegram",
            credentials={"bot_token": bot_token, "api_base_url": f"{api_base}/bot"},
        )
    else:
        connected = Account(
            user_id=account.user_id,
            organization_id=account.organization_id,
            auth_config_id=account.auth_config_id,
            connector_id=account.connector_id,
            provider_account_id=f"e2e-telegram-{surface_name}",
            credentials={"bot_token": bot_token, "api_base_url": f"{api_base}/bot"},
        )
        db_session.add(connected)
        await db_session.commit()
        await db_session.refresh(connected)

    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(connected.id)},
        name=surface_name,
    )
    row = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert row is not None and row.webhook_secret
    return surface["id"], get_secret_cipher().decrypt_str(row.webhook_secret), connected


async def _delivered(
    authenticated_client: AsyncClient,
    *,
    surface_id: str,
    secret: str,
    payload: dict,
) -> SurfaceWebhookReceivedEvent:
    """POST one update to a surface's webhook and return the event it published."""
    published: list[SurfaceWebhookReceivedEvent] = []

    async def _capture(stream: str, event: SurfaceWebhookReceivedEvent) -> None:
        del stream
        published.append(event)

    with patch(
        "app.modules.agent_surfaces.api.controllers.webhook_controller."
        "EventPublisher.publish",
        new=AsyncMock(side_effect=_capture),
    ):
        response = await authenticated_client.post(
            f"/surfaces/{surface_id}/webhook",
            content=json.dumps(payload).encode("utf-8"),
            headers=build_telegram_secret_headers(secret),
        )
    assert response.status_code == 200, response.text
    assert len(published) == 1
    return published[0]


async def _consumed(event: SurfaceWebhookReceivedEvent, queue: _RecordingJobQueue):
    """Run one published event through the real durable inbox, once."""

    async def process() -> None:
        await _process_surface_webhook(
            event,
            logging.getLogger("surface-event-identity-e2e"),
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            job_queue=queue,
        )

    return await provide_domain_event_inbox().process(
        "agent-surfaces.webhook", event, process
    )


@pytest.fixture
def telegram_webhook_mode(monkeypatch):
    """A deployment where Telegram surfaces are driven by signed webhooks."""
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)


async def test_two_bots_numbering_from_one_both_get_answered(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
):
    """The blocker: two pods, two bots, both sending their update number 1.

    Nothing about the two messages is the same except the counter, and the
    counter is per bot. Keyed on ``telegram:1`` alone the second delivery is
    read as a redelivery of the first and dropped in silence.
    """
    pod_id = test_pod["id"]
    user_id = fixed_test_user["id"]
    first_id, first_secret, first_account = await _a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=pod_id,
        user_id=user_id,
        api_base=fake_telegram.api_base,
        bot_token="e2e-first-bot-token",
        surface_name="first-bot",
    )
    second_id, second_secret, _ = await _a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=pod_id,
        user_id=user_id,
        api_base=fake_telegram.api_base,
        bot_token="e2e-second-bot-token",
        surface_name="second-bot",
        account=first_account,
    )

    for sender_id in (6110001, 6110002):
        await _seed_external_user(
            db_session,
            platform="TELEGRAM",
            external_user_id=str(sender_id),
            resolved_user_id=UUID(user_id),
        )

    # Each bot's own update number 1. `_telegram_payload` derives `update_id`
    # from `message_id`, so it is set explicitly: the collision is the point.
    to_first = _telegram_payload(
        text="Ask the first bot", message_id=8801, sender_id=6110001
    )
    to_first["update_id"] = 1
    to_second = _telegram_payload(
        text="Ask the second bot", message_id=9902, sender_id=6110002
    )
    to_second["update_id"] = 1

    first_event = await _delivered(
        authenticated_client,
        surface_id=first_id,
        secret=first_secret,
        payload=to_first,
    )
    second_event = await _delivered(
        authenticated_client,
        surface_id=second_id,
        secret=second_secret,
        payload=to_second,
    )
    assert first_event.source_event_id != second_event.source_event_id
    assert first_event.event_id != second_event.event_id

    queue = _RecordingJobQueue()
    assert await _consumed(first_event, queue) is True
    assert await _consumed(second_event, queue) is True

    answered = {job["payload"]["context"]["surface_id"] for _, job in queue.jobs}
    assert answered == {first_id, second_id}, queue.jobs


async def test_the_same_update_delivered_twice_is_answered_once(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
):
    """The guarantee naming the receiver must not cost: a real redelivery.

    Telegram repeats an update until the webhook answers 200, so the same
    ``update_id`` from the same bot has to reach a job exactly once.
    """
    pod_id = test_pod["id"]
    user_id = fixed_test_user["id"]
    surface_id, secret, _ = await _a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=pod_id,
        user_id=user_id,
        api_base=fake_telegram.api_base,
        bot_token="e2e-repeating-bot-token",
        surface_name="repeating-bot",
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="6110003",
        resolved_user_id=UUID(user_id),
    )

    payload = _telegram_payload(
        text="Say this once", message_id=8803, sender_id=6110003
    )
    payload["update_id"] = 1

    first = await _delivered(
        authenticated_client, surface_id=surface_id, secret=secret, payload=payload
    )
    again = await _delivered(
        authenticated_client, surface_id=surface_id, secret=secret, payload=payload
    )
    assert first.event_id == again.event_id

    queue = _RecordingJobQueue()
    assert await _consumed(first, queue) is True
    assert await _consumed(again, queue) is False
    assert len(queue.jobs) == 1, queue.jobs


class _FailingJobQueue(_RecordingJobQueue):
    """A job queue whose enqueue fails the way a Redis blip does."""

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        raise RuntimeError("job queue is unreachable")


async def test_a_message_whose_enqueue_failed_survives_the_retry(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
):
    """The claim and the job have to stand or fall together.

    ``prepare_ingress`` spends the Redis delivery claim before the job is
    enqueued, so a failure in between used to be permanent: the inbox retries,
    the retry re-enters ``prepare_ingress``, the claim is already spent, and the
    message is dropped as a duplicate of itself. At-least-once for one step
    became at-most-once, and the only trace was a debug line.
    """
    user_id = fixed_test_user["id"]
    surface_id, secret, _ = await _a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=user_id,
        api_base=fake_telegram.api_base,
        bot_token="e2e-unreachable-queue-bot-token",
        surface_name="unreachable-queue-bot",
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="6110004",
        resolved_user_id=UUID(user_id),
    )

    payload = _telegram_payload(
        text="Do not lose this", message_id=8804, sender_id=6110004
    )
    payload["update_id"] = 1
    event = await _delivered(
        authenticated_client, surface_id=surface_id, secret=secret, payload=payload
    )

    with pytest.raises(RuntimeError):
        await _consumed(event, _FailingJobQueue())

    # The inbox left the delivery RETRYING, so this is the redelivery a real
    # worker performs -- and it must reach a job.
    queue = _RecordingJobQueue()
    assert await _consumed(event, queue) is True
    assert len(queue.jobs) == 1, queue.jobs
    assert queue.jobs[0][1]["payload"]["context"]["surface_id"] == surface_id
