from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeReceiverCandidate,
    SurfaceEventReceiverService,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector_account

pytestmark = pytest.mark.e2e


class RecordingReceiverRunner:
    def __init__(
        self,
        candidate: NativeReceiverCandidate,
        events: asyncio.Queue[tuple[str, NativeReceiverCandidate]],
    ) -> None:
        self._candidate = candidate
        self._events = events

    async def run(self) -> None:
        await self._events.put(("started", self._candidate))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self._events.put(("stopped", self._candidate))
            raise


async def test_native_receiver_coordinator_starts_account_backed_telegram_and_slack(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    test_redis_url,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(surface_settings, "enable_slack_socket_mode", True)
    monkeypatch.setattr(surface_settings, "slack_app_token", "xapp-system")

    telegram_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={"bot_token": "telegram-user-token"},
    )
    slack_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-user-workspace",
            "app_token": "xapp-user-app",
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
            },
        },
    )

    events: asyncio.Queue[tuple[str, NativeReceiverCandidate]] = asyncio.Queue()

    def factory(candidate: NativeReceiverCandidate) -> RecordingReceiverRunner:
        return RecordingReceiverRunner(candidate, events)

    service = SurfaceEventReceiverService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        scan_interval_seconds=0.1,
        redis_url=test_redis_url,
        runner_factories={
            SurfacePlatform.TELEGRAM: factory,
            SurfacePlatform.SLACK: factory,
        },
    )
    task = asyncio.create_task(service.run())
    try:
        pod_id = test_pod["id"]
        telegram = await authenticated_client.post(
            f"/pods/{pod_id}/surfaces",
            json={
                "platform": "TELEGRAM",
                "account_id": str(telegram_account.id),
                "credential_mode": "CUSTOM",
            },
        )
        assert telegram.status_code == 200, telegram.text
        slack = await authenticated_client.post(
            f"/pods/{pod_id}/surfaces",
            json={
                "platform": "SLACK",
                "account_id": str(slack_account.id),
                "credential_mode": "CUSTOM",
            },
        )
        assert slack.status_code == 200, slack.text

        started = await _collect_started(events, expected=2)
        started_by_platform = {candidate.platform: candidate for candidate in started}

        telegram_candidate = started_by_platform[SurfacePlatform.TELEGRAM]
        assert telegram_candidate.credentials["bot_token"] == "telegram-user-token"
        assert telegram_candidate.key.startswith(f"telegram:{telegram_account.id}:")
        assert telegram.json()["id"] in {
            str(surface_id) for surface_id in telegram_candidate.surface_ids
        }

        slack_candidate = started_by_platform[SurfacePlatform.SLACK]
        assert slack_candidate.credentials["app_token"] == "xapp-user-app"
        assert slack_candidate.credentials["bot_token"] == "xoxb-user-workspace"
        assert slack_candidate.key.startswith(f"slack:{slack_account.id}:")
        assert slack.json()["id"] in {
            str(surface_id) for surface_id in slack_candidate.surface_ids
        }

        deleted = await authenticated_client.delete(
            f"/pods/{pod_id}/surfaces/telegram"
        )
        assert deleted.status_code == 204, deleted.text
        stopped = await _next_event(events, "stopped")
        assert stopped.platform is SurfacePlatform.TELEGRAM
    finally:
        await service.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _collect_started(
    events: asyncio.Queue[tuple[str, NativeReceiverCandidate]],
    *,
    expected: int,
) -> list[NativeReceiverCandidate]:
    started: list[NativeReceiverCandidate] = []
    while len(started) < expected:
        candidate = await _next_event(events, "started")
        started.append(candidate)
    return started


async def _next_event(
    events: asyncio.Queue[tuple[str, NativeReceiverCandidate]],
    event_name: str,
) -> NativeReceiverCandidate:
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        assert remaining > 0, f"Timed out waiting for receiver event {event_name}"
        name, candidate = await asyncio.wait_for(events.get(), timeout=remaining)
        if name == event_name:
            return candidate
