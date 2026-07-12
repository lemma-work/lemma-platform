from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[3]


def test_removed_schedule_runtime_compatibility_has_no_callers() -> None:
    removed_paths = [
        "modules/schedule/infrastructure/adapters/datastore_adapter.py",
        "modules/schedule/infrastructure/adapters/external_schedule_writer.py",
        "modules/schedule/infrastructure/adapters/composio_webhook_verifier.py",
        "modules/schedule/infrastructure/schedule_managers/composio.py",
        "modules/schedule/infrastructure/schedule_managers/manager_factory.py",
        "modules/schedule/services/schedule_filter_job_service.py",
        "modules/workflow/infrastructure/adapters/agent_adapter.py",
        "modules/workflow/infrastructure/adapters/function_adapter.py",
        "modules/workflow/infrastructure/adapters/schedule_adapter.py",
    ]
    assert not [path for path in removed_paths if (APP_ROOT / path).exists()]

    source = "\n".join(
        path.read_text()
        for path in APP_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    forbidden = [
        "_LEGACY_" + "MISSING_USER_ID",
        "modules.schedule.infrastructure.adapters." + "datastore_adapter",
        "modules.schedule.infrastructure.adapters." + "external_schedule_writer",
        "modules.schedule.infrastructure.adapters." + "composio_webhook_verifier",
        "modules.schedule.infrastructure.schedule_managers." + "composio",
        "modules.schedule.infrastructure.schedule_managers." + "manager_factory",
        "modules.workflow.infrastructure." + "adapters",
    ]
    assert not [value for value in forbidden if value in source]
