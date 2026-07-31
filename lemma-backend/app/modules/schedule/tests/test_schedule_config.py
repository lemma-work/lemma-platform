from app.modules.schedule.config import ScheduleSettings


def test_schedule_settings_own_scheduler_policy(monkeypatch):
    assert set(ScheduleSettings.model_fields) == {
        "scheduler_api_url",
        "schedule_max_consecutive_failures",
        "scheduler_internal_token",
    }
    assert (
        ScheduleSettings.model_fields["scheduler_api_url"].default
        == "http://localhost:8711"
    )
    assert (
        ScheduleSettings.model_fields["schedule_max_consecutive_failures"].default == 5
    )
    assert ScheduleSettings.model_fields["scheduler_internal_token"].default is None

    monkeypatch.setenv("SCHEDULER_API_URL", "http://scheduler:8001")
    monkeypatch.setenv("SCHEDULER_INTERNAL_TOKEN", "canary")
    configured = ScheduleSettings()
    assert configured.scheduler_api_url == "http://scheduler:8001"
    assert configured.scheduler_internal_token is not None
    assert configured.scheduler_internal_token.get_secret_value() == "canary"
