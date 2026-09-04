from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import schedules

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------


def _make_fake_client_and_run(fake_schedules_obj, captured):
    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=fake_schedules_obj)

    fake_client = FakeClient()

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty")
        return fn(fake_client, state)

    return fake_run_with_client


# ---------------------------------------------------------------------------
# 1. schedules list
# ---------------------------------------------------------------------------


def test_schedules_list_dispatches_api(monkeypatch):
    captured = {}

    class FakeSchedules:
        def list(self, **kwargs):
            captured["list_kwargs"] = kwargs
            return {"items": [{"id": "sched-1", "name": "my-schedule"}]}

    fake_run = _make_fake_client_and_run(FakeSchedules(), captured)
    monkeypatch.setattr(schedules, "run_with_client", fake_run)

    result = runner.invoke(app, ["--pod", "pod-1", "schedules", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my-schedule" in result.stdout


# ---------------------------------------------------------------------------
# 2. schedules get
# ---------------------------------------------------------------------------


def test_schedules_get_dispatches_api(monkeypatch):
    captured = {}

    class FakeSchedules:
        def get(self, schedule):
            captured["schedule"] = schedule
            return {"id": schedule, "name": "my-schedule"}

    fake_run = _make_fake_client_and_run(FakeSchedules(), captured)
    monkeypatch.setattr(schedules, "run_with_client", fake_run)

    result = runner.invoke(app, ["schedules", "get", "my-schedule", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("schedule") == "my-schedule"


# ---------------------------------------------------------------------------
# 3. schedules delete --yes
# ---------------------------------------------------------------------------


def test_schedules_delete_with_yes(monkeypatch):
    captured = {}

    class FakeSchedules:
        def delete(self, schedule):
            captured["deleted"] = schedule
            return

    fake_run = _make_fake_client_and_run(FakeSchedules(), captured)
    monkeypatch.setattr(schedules, "run_with_client", fake_run)

    result = runner.invoke(
        app, ["schedules", "delete", "my-schedule", "--yes", "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "my-schedule"


# ---------------------------------------------------------------------------
# 4. schedules delete without --yes in non-interactive mode
# ---------------------------------------------------------------------------


def test_schedules_delete_requires_yes(monkeypatch):
    captured = {}

    class FakeSchedules:
        def delete(self, schedule):
            captured["deleted"] = schedule
            return

    fake_run = _make_fake_client_and_run(FakeSchedules(), captured)
    monkeypatch.setattr(schedules, "run_with_client", fake_run)

    # CliRunner is non-interactive; confirm_destructive should call fail().
    result = runner.invoke(
        app, ["schedules", "delete", "my-schedule", "--pod", "pod-1"]
    )

    assert result.exit_code != 0
    assert "deleted" not in captured


# ---------------------------------------------------------------------------
# 5. schedules create --workflow --cron (TIME schedule)
# ---------------------------------------------------------------------------


def test_schedule_create_workflow_cron_dispatches_api(monkeypatch):
    captured = {}

    class FakeSchedules:
        def create(self, request):
            captured["request"] = request.to_dict()
            return {"id": "sched-new"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=FakeSchedules())

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty")
        return fn(FakeClient(), state)

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "create",
            "--name",
            "wf_cron",
            "--workflow",
            "my-wf",
            "--cron",
            "0 9 * * 1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["request"]["workflow_name"] == "my-wf"
    assert captured["request"]["schedule_type"] == "TIME"
    assert captured["request"]["config"].get("cron") == "0 9 * * 1"


# ---------------------------------------------------------------------------
# 6. schedules create --agent --connector-trigger (CONNECTOR_TRIGGER schedule)
#    The schedules.create command uses webhook source + connector-trigger to
#    produce a WEBHOOK-type schedule with connector_trigger_id set.
# ---------------------------------------------------------------------------


def test_schedule_create_connector_trigger_dispatches_api(monkeypatch):
    captured = {}

    class FakeSchedules:
        def create(self, request):
            captured["request"] = request.to_dict()
            return {"id": "sched-ct"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=FakeSchedules())

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty")
        return fn(FakeClient(), state)

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "create",
            "--name",
            "connector_sched",
            "--agent",
            "my-agent",
            "--webhook-source",
            "composio",
            "--connector-trigger",
            "ct-123",
        ],
    )

    assert result.exit_code == 0, result.stdout
    req = captured["request"]
    assert req["connector_trigger_id"] == "ct-123"
    assert req["schedule_type"] == "WEBHOOK"
    assert req["agent_name"] == "my-agent"


def test_schedules_list_shows_the_target(monkeypatch):
    """The list had an `Agent Id` column and nothing for workflows, so a
    workflow-targeted schedule showed a blank target and you had to run
    `schedules get` just to learn what it starts."""
    from types import SimpleNamespace

    from lemma_cli.cli_core.commands import schedules as schedules_mod

    items = [
        {"id": "s1", "name": "hourly-sweep", "agent_name": "support-triage"},
        {"id": "s2", "name": "on-new-ticket", "workflow_name": "auto-triage"},
    ]

    class FakeSchedules:
        def list(self, **kwargs):
            return {"items": items}

    monkeypatch.setattr(
        schedules_mod,
        "run_with_client",
        lambda ctx, fn: fn(
            SimpleNamespace(
                pod=lambda pod_id: SimpleNamespace(schedules=FakeSchedules())
            ),
            SimpleNamespace(
                config={"_runtime": {"pod": "pod-1"}}, output="pretty", full=False
            ),
        ),
    )

    result = runner.invoke(app, ["schedules", "list", "--pod", "pod-1"])

    assert result.exit_code == 0, result.output
    assert "agent:support-triage" in result.stdout
    assert "workflow:auto-triage" in result.stdout


def test_schedules_list_json_is_untouched_by_the_target_column(monkeypatch):
    """`--json` must stay the API payload, not a rendering convenience."""
    import json
    from types import SimpleNamespace

    from lemma_cli.cli_core.commands import schedules as schedules_mod

    class FakeSchedules:
        def list(self, **kwargs):
            return {"items": [{"id": "s2", "name": "x", "workflow_name": "w"}]}

    monkeypatch.setattr(
        schedules_mod,
        "run_with_client",
        lambda ctx, fn: fn(
            SimpleNamespace(
                pod=lambda pod_id: SimpleNamespace(schedules=FakeSchedules())
            ),
            SimpleNamespace(
                config={"_runtime": {"pod": "pod-1"}}, output="json", full=False
            ),
        ),
    )

    result = runner.invoke(app, ["--json", "schedules", "list", "--pod", "pod-1"])

    assert result.exit_code == 0, result.output
    assert "target" not in json.loads(result.stdout)["items"][0]


# ---------------------------------------------------------------------------
# update: the wake instruction, and fields that do not exist
# ---------------------------------------------------------------------------


def _update_capture(monkeypatch):
    captured = {}

    class FakeSchedules:
        def update(self, schedule, request):
            captured["schedule"] = schedule
            captured["payload"] = request.to_dict()
            return {"id": "sched-1", "name": schedule}

    monkeypatch.setattr(
        schedules,
        "run_with_client",
        _make_fake_client_and_run(FakeSchedules(), captured),
    )
    return captured


def test_schedules_update_takes_the_instruction_as_a_flag(monkeypatch):
    """Retargeting at the assistant needed a hand-written JSON body, because
    `update` had no flags at all -- so the one field the API then demands could
    only be supplied by someone who already knew its name."""
    captured = _update_capture(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "update",
            "wellness",
            "--agent",
            "POD_DEFAULT",
            "--instruction",
            "Ask how they are doing.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["payload"]["agent_name"] == "POD_DEFAULT"
    assert captured["payload"]["instruction"] == "Ask how they are doing."


def test_schedules_update_warns_on_an_unrecognized_field(monkeypatch):
    """`create` warned and `update` did not, so a payload naming the wrong key
    was applied as an empty update and reported success -- which reads as "the
    field was accepted", and sent the caller looking somewhere else."""
    captured = _update_capture(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "update",
            "wellness",
            "--data",
            '{"data": "wake up and do the thing"}',
        ],
    )

    assert result.exit_code == 0, result.output
    assert "data" in result.output
    assert "instruction" not in captured["payload"]


def test_schedules_update_refuses_to_do_nothing(monkeypatch):
    """An update with no flags and no payload used to be a required-arg error
    from `--data`; now that flags exist it has to say what is actually wrong."""
    _update_capture(monkeypatch)

    result = runner.invoke(app, ["--pod", "pod-1", "schedules", "update", "wellness"])

    assert result.exit_code != 0
    assert "Nothing to update" in result.output
