import pytest
from pydantic import ValidationError

from app.modules.schedule.config import ScheduleSettings


def test_schedule_settings_own_scheduler_policy(monkeypatch):
    assert set(ScheduleSettings.model_fields) == {
        "scheduler_api_url",
        "schedule_max_consecutive_failures",
        "schedule_minimum_interval_minutes",
        "schedule_run_retention_days",
        "schedule_run_retention_batch_size",
        "schedule_run_retention_budget_seconds",
    }
    assert (
        ScheduleSettings.model_fields["scheduler_api_url"].default
        == "http://localhost:8711"
    )
    assert (
        ScheduleSettings.model_fields["schedule_max_consecutive_failures"].default == 5
    )
    assert (
        ScheduleSettings.model_fields["schedule_minimum_interval_minutes"].default == 15
    )

    monkeypatch.setenv("SCHEDULER_API_URL", "http://scheduler:8001")
    configured = ScheduleSettings()
    assert configured.scheduler_api_url == "http://scheduler:8001"


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
