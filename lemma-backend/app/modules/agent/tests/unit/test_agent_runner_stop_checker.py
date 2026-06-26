"""Unit tests for the throttled/sticky stop checker in AgentRunnerService.

The harness polls ``should_stop`` at every streaming checkpoint (per token). The
checker must NOT issue a DB query on every call — it caches the result and
re-queries at most once per ``agent_run_stop_poll_interval_seconds``, and once a
stop is observed it sticks without further queries.
"""

from uuid import uuid4

from app.core.config import settings
from app.modules.agent.services import agent_runner_service as ars_module
from app.modules.agent.services.agent_runner_service import AgentRunnerService


def _service() -> AgentRunnerService:
    return AgentRunnerService(uow_factory=lambda: None, harness_registry=object())


async def test_stop_checker_throttles_db_polls(monkeypatch):
    service = _service()
    calls = {"n": 0}

    async def fake_should_stop(_run_id):
        calls["n"] += 1
        return False

    monkeypatch.setattr(service, "_should_stop_run", fake_should_stop)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ars_module.time, "monotonic", lambda: clock["t"])

    interval = settings.agent_run_stop_poll_interval_seconds
    check = service._make_stop_checker(uuid4())

    # First checkpoint queries the DB.
    assert await check() is False
    assert calls["n"] == 1

    # Many rapid checkpoints within the interval reuse the cached answer.
    for _ in range(100):
        assert await check() is False
    assert calls["n"] == 1

    # Crossing the interval triggers exactly one more query.
    clock["t"] += interval + 0.01
    assert await check() is False
    assert calls["n"] == 2


async def test_stop_checker_is_sticky_once_stopped(monkeypatch):
    service = _service()
    answers = iter([False, True])
    calls = {"n": 0}

    async def fake_should_stop(_run_id):
        calls["n"] += 1
        return next(answers)

    monkeypatch.setattr(service, "_should_stop_run", fake_should_stop)
    clock = {"t": 0.0}
    monkeypatch.setattr(ars_module.time, "monotonic", lambda: clock["t"])

    interval = settings.agent_run_stop_poll_interval_seconds
    check = service._make_stop_checker(uuid4())

    assert await check() is False  # query 1 -> not stopped
    clock["t"] += interval + 0.01
    assert await check() is True  # query 2 -> stopped
    assert calls["n"] == 2

    # Sticky: stays stopped without any further DB queries, even much later.
    clock["t"] += 100.0
    assert await check() is True
    assert await check() is True
    assert calls["n"] == 2
