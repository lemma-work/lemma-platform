import pytest
from pydantic import ValidationError

from app.modules.schedule.config import BREAKER_STREAK_SCAN_LIMIT, ScheduleSettings
from app.modules.schedule.repositories.schedule_run_repository import (
    _BREAKER_SCAN_LIMIT,
)


def test_schedule_settings_own_scheduler_policy(monkeypatch):
    assert set(ScheduleSettings.model_fields) == {
        "schedule_max_consecutive_failures",
        "schedule_minimum_interval_minutes",
        "schedule_run_retention_days",
        "schedule_run_retention_batch_size",
        "schedule_run_retention_budget_seconds",
        "schedule_run_reinspect_after_minutes",
    }
    assert (
        ScheduleSettings.model_fields["schedule_max_consecutive_failures"].default == 5
    )
    assert (
        ScheduleSettings.model_fields["schedule_minimum_interval_minutes"].default == 15
    )


def test_the_scheduler_api_url_is_gone(monkeypatch):
    """``SCHEDULER_API_URL`` addressed the scheduler service, which was deleted.

    It pointed callers at the `/scheduler/jobs` control plane. That plane is
    gone -- no ``app/scheduler.py``, no router, no routes -- and nothing in the
    backend read this setting any more. Schedules are fired by the poller inside
    the embedded worker. Setting it must be inert rather than look like
    configuration that still reaches something.
    """
    monkeypatch.setenv("SCHEDULER_API_URL", "http://scheduler:8001")

    assert "scheduler_api_url" not in ScheduleSettings().model_dump()


def test_the_scheduler_sidecar_token_is_gone(monkeypatch):
    """``SCHEDULER_INTERNAL_TOKEN`` belonged to a service that no longer exists.

    Its only reader was ``schedule/scheduler/internal_auth.py``, which nothing
    imported, and the ``app/scheduler.py`` its docstring referred to was deleted
    with the APScheduler removal. Both environments still carried the secret.
    Setting it must now be inert rather than quietly configuring nothing.
    """
    monkeypatch.setenv("SCHEDULER_INTERNAL_TOKEN", "canary")

    assert "scheduler_internal_token" not in ScheduleSettings().model_dump()


def test_schedule_minimum_interval_must_be_positive(monkeypatch):
    monkeypatch.setenv("SCHEDULE_MINIMUM_INTERVAL_MINUTES", "0")
    with pytest.raises(ValidationError):
        ScheduleSettings()


def test_a_breaker_threshold_deeper_than_the_streak_scan_is_refused(monkeypatch):
    """A threshold past the scan depth is a breaker that can never trip.

    ``consecutive_terminal_failures`` counts back over at most
    ``BREAKER_STREAK_SCAN_LIMIT`` rows, so the number it returns can never reach
    a threshold set beyond that. Nothing downstream notices: every completion
    computes a streak, compares it, finds it short, and the schedule retries a
    broken target forever. Refusing at startup is the only place this is visible.
    """
    monkeypatch.setenv(
        "SCHEDULE_MAX_CONSECUTIVE_FAILURES", str(BREAKER_STREAK_SCAN_LIMIT + 1)
    )
    with pytest.raises(ValidationError):
        ScheduleSettings()

    monkeypatch.setenv(
        "SCHEDULE_MAX_CONSECUTIVE_FAILURES", str(BREAKER_STREAK_SCAN_LIMIT)
    )
    assert ScheduleSettings().schedule_max_consecutive_failures == (
        BREAKER_STREAK_SCAN_LIMIT
    )


def test_the_streak_scan_limit_has_one_definition():
    """The repository must not carry a second copy of the number.

    Two copies is exactly how the bound above becomes a lie: config validates
    against 200 while the query reads 50, and the breaker quietly stops working
    for any threshold between them.
    """
    assert _BREAKER_SCAN_LIMIT is BREAKER_STREAK_SCAN_LIMIT
