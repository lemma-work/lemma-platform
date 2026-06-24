from __future__ import annotations

import json

import pytest
import typer
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import (
    agents,
    conversations,
    data,
    apps,
    files,
    functions,
    connectors,
    organizations,
    pods,
    schedules,
    surfaces,
    tools,
    workflows,
)
from lemma_cli.cli_core.chat import iter_sse_events
from lemma_cli.cli_app.pod_bundle import (
    _normalize_function_payload,
    _sanitize_function_payload_for_import,
)


runner = CliRunner()


def test_iter_sse_events_supports_httpx_iter_lines_signature():
    class Response:
        def iter_lines(self):
            yield 'data: {"type":"token","data":"hello"}'
            yield ""
            yield 'data: {"type":"completed","data":{"status":"completed"}}'
            yield ""

    events = list(iter_sse_events(Response()))

    assert [event.type for event in events] == ["token", "completed"]
    assert events[0].data == "hello"


def write_app_template_fixture(tmp_path):
    template = tmp_path / "app-template"
    files = {
        "package.json": json.dumps(
            {
                "name": "lemma-vite-starter",
                "private": True,
                "type": "module",
                "scripts": {"dev": "vite", "build": "vite build"},
            },
            indent=2,
        )
        + "\n",
        "components.json": "{}\n",
        "index.html": "<title>Lemma Vite Starter</title>\n",
        "vite.config.ts": (
            "import { defineConfig } from 'vite'\n"
            "export default defineConfig({\n"
            "  plugins: [],\n"
            "})\n"
        ),
        "src/lib/lemma-client.ts": (
            "export const lemmaAppName = "
            "import.meta.env.VITE_LEMMA_APP_NAME ?? 'Lemma Vite Starter'\n"
        ),
        "src/components/layout/app-title.tsx": (
            "export const title = 'Lemma Starter'\n"
            "export const subtitle = 'Vite + Lemma SDK'\n"
        ),
        "src/components/layout/data/sidebar-data.ts": "export const team = 'Lemma Starter'\n",
        "src/context/theme-provider.tsx": (
            "export type StylePreset = 'default' | 'editorial' | 'soft' | 'terminal' | 'neobrutal'\n"
            "const DEFAULT_STYLE_PRESET: StylePreset = 'default'\n"
        ),
        "src/routes/_authenticated/route.tsx": "export const route = 'auth'\n",
        ".env": "VITE_LEMMA_API_URL=http://should-not-copy\n",
        ".git/config": "[core]\n",
        "node_modules/placeholder.txt": "ignored\n",
    }
    for relative_path, content in files.items():
        path = template / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return template


def test_help_exposes_new_surface_only():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.stdout
    assert "schedules" in result.stdout
    assert "conversations" in result.stdout
    assert "functions" in result.stdout
    assert "apps" in result.stdout
    assert "connectors" in result.stdout
    assert "orgs" in result.stdout
    assert "surfaces" in result.stdout
    assert "tools" in result.stdout
    assert "servers" in result.stdout
    assert "ctx" not in result.stdout
    assert "│ use " not in result.stdout
    assert "assistant" not in result.stdout
    assert "workflow install" not in result.stdout
    assert "trigger" not in result.stdout


def test_schedule_create_agent_cron_dispatches_schedule_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSchedules:
        def create(self, request):
            captured["request"] = request.to_dict()
            return {"id": "schedule-1"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=FakeSchedules())

    fake_client = FakeClient()

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "create",
            "--name",
            "triage_cron",
            "--agent",
            "triage",
            "--cron",
            "*/5 * * * *",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "pod_id": "pod-1",
        "request": {
            "name": "triage_cron",
            "schedule_type": "TIME",
            "agent_name": "triage",
            "workflow_name": None,
            "config": {"cron": "*/5 * * * *"},
            "account_id": None,
            "connector_trigger_id": None,
            "filter_instruction": None,
            "filter_output_schema": None,
        },
    }


def test_global_json_flag_applies_to_schedule_commands(monkeypatch):
    class FakeSchedules:
        def list(self, **kwargs):
            return {"items": [{"id": "schedule-1", "name": "triage_cron"}], "limit": 100}

    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(schedules=FakeSchedules())

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "schedules", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["items"] == [{"id": "schedule-1", "name": "triage_cron"}]


def test_function_bundle_payload_excludes_response_only_fields():
    response_payload = {
        "id": "function-id",
        "pod_id": "pod-id",
        "user_id": "user-id",
        "name": "adder",
        "code": "def adder(input):\n    return input\n",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "config_schema": {"type": "object"},
        "allowed_actions": ["function.read"],
        "created_at": "2026-06-07T00:00:00Z",
        "updated_at": "2026-06-07T00:00:00Z",
    }

    assert _normalize_function_payload(response_payload) == {
        "name": "adder",
        "code": "def adder(input):\n    return input\n",
    }
    assert _sanitize_function_payload_for_import(response_payload) == {
        "name": "adder",
        "code": "def adder(input):\n    return input\n",
    }


def test_orgs_list_dispatches_organizations_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOrganizations:
        def list(self, *, limit=100):
            captured["limit"] = limit
            return {"items": [{"id": "org-1", "name": "Acme"}]}

    fake_client = SimpleNamespace(organizations=FakeOrganizations())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"defaults": {"org_id": "org-1"}}))

    monkeypatch.setattr(organizations, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["orgs", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"limit": 5}
    assert "Acme" in result.stdout
    assert "yes" in result.stdout


def test_pods_list_marks_selected_pod(monkeypatch):
    captured: dict[str, object] = {}

    class FakePods:
        def list(self, *, org_id, limit=100, page_token=None):
            captured["org_id"] = org_id
            captured["limit"] = limit
            return {
                "items": [
                    {"id": "pod-1", "name": "Ops", "organization_id": org_id},
                    {"id": "pod-2", "name": "Sales", "organization_id": org_id},
                ]
            }

    fake_client = SimpleNamespace(pods=FakePods())

    def fake_run_with_client(ctx, fn):
        return fn(
            fake_client,
            SimpleNamespace(
                config={"defaults": {"org_id": "org-1", "pod_id": "pod-2"}}
            ),
        )

    monkeypatch.setattr(pods, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["pods", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"org_id": "org-1", "limit": 5}
    assert "Sales" in result.stdout
    assert "yes" in result.stdout


def test_tables_list_dispatches_tables_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTables:
        def list(self, pod_id, *, limit=100):
            captured["pod_id"] = pod_id
            captured["limit"] = limit
            return {
                "items": [
                    {
                        "name": "calendar_events",
                        "primary_key_column": "id",
                        "columns": [
                            {"name": "id", "type": "UUID", "required": True},
                            {"name": "title", "type": "TEXT", "required": True},
                            {"name": "starts_at", "type": "TIMESTAMP"},
                            {"name": "created_at", "type": "TIMESTAMP", "system": True},
                        ],
                    }
                ]
            }

    fake_client = SimpleNamespace(tables=FakeTables())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(data, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", "pod-1", "tables", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "limit": 5}
    assert "title:text" in result.stdout
    assert "starts_at:timestamp" in result.stdout
    assert "created_at:timestamp" not in result.stdout
    assert "'name': 'title'" not in result.stdout


def test_tables_get_dispatches_tables_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTables:
        def get(self, pod_id, table):
            captured["pod_id"] = pod_id
            captured["table"] = table
            return {
                "name": table,
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID", "required": True},
                    {"name": "summary", "type": "TEXT"},
                ],
            }

    fake_client = SimpleNamespace(tables=FakeTables())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(data, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", "pod-1", "tables", "get", "calendar_events"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "table": "calendar_events"}
    assert "id:uuid" in result.stdout
    assert "summary:text" in result.stdout
    assert "2 items" not in result.stdout


def test_records_list_dispatches_records_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeRecords:
        def list(self, pod_id, table, *, limit=20):
            captured["pod_id"] = pod_id
            captured["table"] = table
            captured["limit"] = limit
            return {"items": []}

    fake_client = SimpleNamespace(records=FakeRecords())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(data, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--pod", "pod-1", "records", "list", "calendar_events", "--limit", "5"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "table": "calendar_events", "limit": 5}


def test_pods_describe_renders_table_column_summary(monkeypatch):
    class FakeResource:
        def __init__(self, payload):
            self.payload = payload

        def list(self, *, limit=50):
            return self.payload

    class FakeFiles:
        def tree(self, path):
            return {"tree": {"children": []}}

    class FakePod:
        tables = FakeResource(
            {
                "items": [
                    {
                        "name": "tickets",
                        "primary_key_column": "id",
                        "columns": [
                            {"name": "id", "type": "UUID", "required": True},
                            {"name": "title", "type": "TEXT", "required": True},
                            {"name": "status", "type": "ENUM"},
                        ],
                    }
                ]
            }
        )
        functions = FakeResource({"items": []})
        agents = FakeResource({"items": []})
        workflows = FakeResource({"items": []})
        schedules = FakeResource({"items": []})
        files = FakeFiles()

    pod_uuid = "11111111-1111-1111-1111-111111111111"

    class FakePods:
        def get(self, pod_id):
            return {"id": pod_id, "name": "Ops"}

    class FakeClient:
        pods = FakePods()

        def pod(self, pod_id):
            return FakePod()

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": pod_uuid}}))

    monkeypatch.setattr(pods, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", pod_uuid, "pods", "describe"])

    assert result.exit_code == 0, result.stdout
    assert "tickets" in result.stdout
    assert "title:text" in result.stdout
    assert "status:enum" in result.stdout
    assert "Columns" in result.stdout


POD_UUID = "11111111-1111-1111-1111-111111111111"


def _resolver_client(items, calls, *, page_size=None):
    class FakePods:
        def list(self, *, org_id=None, limit=100, page_token=None):
            calls.append({"org_id": org_id, "limit": limit, "page_token": page_token})
            if page_size is None:
                return {"items": items, "next_page_token": None}
            start = int(page_token or 0)
            chunk = items[start : start + page_size]
            nxt = start + page_size
            return {
                "items": chunk,
                "next_page_token": str(nxt) if nxt < len(items) else None,
            }

    return SimpleNamespace(pods=FakePods())


def _resolver_state(pod_id):
    return SimpleNamespace(config={"_runtime": {"pod": pod_id}})


def test_resolve_pod_id_passes_uuid_through_without_listing():
    """A UUID selector must short-circuit: stored defaults are already UUIDs, so
    the common path never pays for a `pods list` round-trip."""
    calls: list[dict] = []
    client = _resolver_client([], calls)

    resolved = pods.resolve_pod_id(client, _resolver_state(POD_UUID))

    assert resolved == POD_UUID
    assert calls == []


def test_resolve_pod_id_resolves_name_via_list():
    calls: list[dict] = []
    client = _resolver_client(
        [
            {"id": POD_UUID, "name": "Ops", "slug": "ops"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "Sales"},
        ],
        calls,
    )

    resolved = pods.resolve_pod_id(client, _resolver_state("Ops"))

    assert resolved == POD_UUID
    assert len(calls) == 1


def test_resolve_pod_id_name_match_is_case_insensitive():
    calls: list[dict] = []
    client = _resolver_client([{"id": POD_UUID, "name": "Ops", "slug": "ops"}], calls)

    assert pods.resolve_pod_id(client, _resolver_state("ops")) == POD_UUID


def test_resolve_pod_id_paginates_beyond_the_first_page():
    """A pod whose name sorts past the first page must still resolve (the old
    hardcoded single `limit=200` page silently dropped it)."""
    calls: list[dict] = []
    target = "33333333-3333-3333-3333-333333333333"
    items = [{"id": f"{i:08d}-0000-0000-0000-000000000000", "name": f"pod{i}"} for i in range(450)]
    items.append({"id": target, "name": "needle"})  # 451st item, on page 3
    client = _resolver_client(items, calls, page_size=200)

    assert pods.resolve_pod_id(client, _resolver_state("needle")) == target
    assert len(calls) == 3  # paged through all three pages


def test_resolve_pod_id_errors_when_name_not_found():
    calls: list[dict] = []
    client = _resolver_client([{"id": POD_UUID, "name": "Ops"}], calls)

    with pytest.raises(typer.Exit):
        pods.resolve_pod_id(client, _resolver_state("nope"))


def test_resolve_pod_id_errors_when_name_is_ambiguous():
    calls: list[dict] = []
    client = _resolver_client(
        [
            {"id": POD_UUID, "name": "Ops"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "ops"},
        ],
        calls,
    )

    with pytest.raises(typer.Exit):
        pods.resolve_pod_id(client, _resolver_state("Ops"))


def test_pods_get_resolves_pod_by_name(monkeypatch):
    """End-to-end: `lemma pods get <name>` must resolve the name to a UUID
    instead of failing with "badly formed hexadecimal UUID string"."""
    seen: dict[str, object] = {}

    class FakePods:
        def list(self, *, org_id=None, limit=100, page_token=None):
            return {"items": [{"id": POD_UUID, "name": "Ops", "slug": "ops"}]}

        def get(self, pod_id):
            seen["pod_id"] = pod_id
            return {"id": pod_id, "name": "Ops"}

    fake_client = SimpleNamespace(pods=FakePods())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={}, output="pretty"))

    monkeypatch.setattr(pods, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["pods", "get", "Ops"])

    assert result.exit_code == 0, result.stdout
    assert seen["pod_id"] == POD_UUID


def test_schedule_create_forwards_connector_trigger_from_payload(monkeypatch):
    captured: dict[str, object] = {}
    payload = json.dumps(
        {
            "connector_trigger_id": "gmail:gmail_new_gmail_message",
            "config": {"labelIds": "INBOX"},
        }
    )

    class FakeSchedules:
        def create(self, request):
            captured["request"] = request.to_dict()
            return {"id": "schedule-1"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=FakeSchedules())

    fake_client = FakeClient()

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "create",
            "--workflow",
            "gmail-email-ingest",
            "--webhook-source",
            "composio",
            "--account",
            "account-1",
            "--data",
            payload,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["request"] == {
        "name": None,
        "schedule_type": "WEBHOOK",
        "agent_name": None,
        "workflow_name": "gmail-email-ingest",
        "config": {"source": "composio", "labelIds": "INBOX"},
        "account_id": "account-1",
        "connector_trigger_id": "gmail:gmail_new_gmail_message",
        "filter_instruction": None,
        "filter_output_schema": None,
    }


def test_workflow_update_graph_dispatches_workflow_api(monkeypatch):
    captured: dict[str, object] = {}
    payload = json.dumps(
        {
            "nodes": [{"id": "end", "type": "END"}],
            "edges": [],
            "start": {"type": "MANUAL", "config": None},
        }
    )

    class FakeWorkflows:
        def update_graph(self, workflow, request):
            captured["workflow"] = workflow
            captured["request"] = request
            return {"id": "workflow-1"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(workflows=FakeWorkflows())

    fake_client = FakeClient()

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(workflows, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "workflows",
            "update-graph",
            "intake",
            "--data",
            payload,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "pod_id": "pod-1",
        "workflow": "intake",
        "request": {
            "nodes": [{"id": "end", "type": "END"}],
            "edges": [],
            "start": {"type": "MANUAL", "config": None},
        },
    }


def test_workflow_submit_form_dispatches_workflow_api(monkeypatch):
    captured: dict[str, object] = {}
    payload = json.dumps({"approved": True})

    class FakeWorkflows:
        def run_get(self, run):
            return {
                "id": run,
                "status": "WAITING",
                "active_wait": {"wait_type": "HUMAN", "node_id": "approval"},
            }

        def submit_form(self, run, *, node_id, inputs=None):
            captured["run"] = run
            captured["node_id"] = node_id
            captured["inputs"] = inputs
            return {"id": run, "status": "COMPLETED"}

    class FakePod:
        pod_id = "pod-1"
        workflows = FakeWorkflows()

    fake_client = SimpleNamespace(pod=lambda pod_id: FakePod())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(workflows, "run_with_client", fake_run_with_client)
    monkeypatch.setattr(workflows, "pod_client", lambda client, s, pod=None: FakePod())

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "workflows",
            "runs",
            "submit-form",
            "run-1",
            "--data",
            payload,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "run": "run-1",
        "node_id": "approval",
        "inputs": {"approved": True},
    }


def test_workflow_cancel_run_dispatches_workflow_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeWorkflows:
        def cancel_run(self, run):
            captured["run"] = run
            return {"id": run, "status": "CANCELLED"}

    class FakePod:
        pod_id = "pod-1"
        workflows = FakeWorkflows()

    def fake_run_with_client(ctx, fn):
        return fn(
            SimpleNamespace(pod=lambda pod_id: FakePod()),
            SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"),
        )

    monkeypatch.setattr(workflows, "run_with_client", fake_run_with_client)
    monkeypatch.setattr(workflows, "pod_client", lambda client, s, pod=None: FakePod())

    result = runner.invoke(
        app,
        ["--pod", "pod-1", "workflows", "runs", "cancel", "run-1"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"run": "run-1"}


def test_function_permissions_replace_dispatches_permissions_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFunctions:
        def replace_permissions(self, pod_id, function, payload):
            captured["pod_id"] = pod_id
            captured["function"] = function
            captured["payload"] = payload
            return {"function_name": function, "grants": payload["grants"]}

    fake_client = SimpleNamespace(functions=FakeFunctions())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(functions, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--output",
            "json",
            "functions",
            "permissions",
            "replace",
            "adder",
            "--data",
            json.dumps(
                {
                    "grants": [
                        {
                            "resource_type": "connector",
                            "resource_name": "gmail",
                            "permission_ids": ["connector.use"],
                        }
                    ]
                }
            ),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "pod_id": "pod-1",
        "function": "adder",
        "payload": {
            "grants": [
                {
                    "resource_type": "connector",
                    "resource_name": "gmail",
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    }


def test_functions_run_dispatches_function_run_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFunctions:
        def run(self, pod_id, function, *, input_data=None):
            captured["pod_id"] = pod_id
            captured["function"] = function
            captured["input_data"] = input_data
            return {"id": "run-1", "status": "SUCCEEDED"}

    fake_client = SimpleNamespace(functions=FakeFunctions())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(functions, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--output",
            "json",
            "functions",
            "run",
            "adder",
            "--data",
            json.dumps({"x": 2}),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "pod_id": "pod-1",
        "function": "adder",
        "input_data": {"x": 2},
    }


def test_functions_runs_list_dispatches_function_runs_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFunctions:
        def runs(self, pod_id, function, *, limit=100):
            captured["pod_id"] = pod_id
            captured["function"] = function
            captured["limit"] = limit
            return {"items": [], "limit": limit}

    fake_client = SimpleNamespace(functions=FakeFunctions())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(functions, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--output", "json", "functions", "runs", "list", "adder", "--limit", "5"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "function": "adder", "limit": 5}


def test_functions_runs_get_dispatches_function_run_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFunctions:
        def run_get(self, pod_id, function, run_id):
            captured["pod_id"] = pod_id
            captured["function"] = function
            captured["run_id"] = run_id
            return {"id": run_id, "status": "COMPLETED"}

    fake_client = SimpleNamespace(functions=FakeFunctions())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(functions, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--output", "json", "functions", "runs", "get", "adder", "run-9"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "function": "adder", "run_id": "run-9"}


def test_agent_permissions_get_dispatches_permissions_api(monkeypatch):
    captured = {}

    class FakeAgents:
        def get_permissions(self, pod_id, agent):
            captured["pod_id"] = pod_id
            captured["agent"] = agent
            return {"agent_name": agent, "grants": []}

    fake_client = SimpleNamespace(agents=FakeAgents())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(agents, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--output",
            "json",
            "agents",
            "permissions",
            "get",
            "triage",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "agent": "triage"}


def test_schedule_create_agent_webhook_forwards_connector_trigger_option(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSchedules:
        def create(self, request):
            captured["request"] = request.to_dict()
            return {"id": "schedule-1"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(schedules=FakeSchedules())

    fake_client = FakeClient()

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(schedules, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "schedules",
            "create",
            "--agent",
            "triage",
            "--webhook-source",
            "slack",
            "--connector-trigger",
            "slack:message_created",
            "--account",
            "account-1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["request"] == {
        "name": None,
        "schedule_type": "WEBHOOK",
        "agent_name": "triage",
        "workflow_name": None,
        "config": {"source": "slack"},
        "account_id": "account-1",
        "connector_trigger_id": "slack:message_created",
        "filter_instruction": None,
        "filter_output_schema": None,
    }


def test_file_upload_me_path_uses_path_only(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    local = tmp_path / "report.pdf"
    local.write_text("data", encoding="utf-8")

    class FakeFiles:
        def upload(self, pod_id, **kwargs):
            captured["pod_id"] = pod_id
            captured["kwargs"] = kwargs
            return {"path": "/me/reports/report.pdf"}

    fake_client = SimpleNamespace(files=FakeFiles())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(files, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--pod", "pod-1", "files", "upload", str(local), "/me/reports/report.pdf"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["kwargs"] == {
        "file_path": str(local),
        "directory_path": "/me/reports",
        "name": "report.pdf",
        "description": None,
        "search_enabled": True,
    }


def test_file_download_passes_clean_path(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    local = tmp_path / "downloaded.pdf"

    class FakeFiles:
        def download(self, pod_id, path, **kwargs):
            captured["pod_id"] = pod_id
            captured["path"] = path
            captured["kwargs"] = kwargs
            local.write_bytes(b"pdf")
            return str(local)

    fake_client = SimpleNamespace(files=FakeFiles())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(files, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "files",
            "download",
            "/PRODUCT_DOCUMENTS_LIBRARY/spec.pdf",
            str(local),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["path"] == "/PRODUCT_DOCUMENTS_LIBRARY/spec.pdf"
    assert captured["kwargs"] == {
        "output_path": str(local),
    }


def test_file_download_markdown_passes_clean_path(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    local = tmp_path / "spec.md"

    class FakeFiles:
        def download_markdown(self, pod_id, path, **kwargs):
            captured["pod_id"] = pod_id
            captured["path"] = path
            captured["kwargs"] = kwargs
            return str(local)

    fake_client = SimpleNamespace(files=FakeFiles())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(files, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "files",
            "download",
            "/PRODUCT_DOCUMENTS_LIBRARY/spec.pdf",
            str(local),
            "--markdown",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["path"] == "/PRODUCT_DOCUMENTS_LIBRARY/spec.pdf"
    assert captured["kwargs"] == {"output_path": str(local)}


def test_chat_creates_agent_conversation_and_streams_message(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConversations:
        def create(self, pod_id, payload):
            captured["create"] = (pod_id, payload)
            return {"id": "conversation-1"}

        def send_message(self, pod_id, conversation_id, *, content, stream):
            captured["send"] = (pod_id, conversation_id, content, stream)

            class Response:
                def iter_lines(self, decode_unicode=True):
                    yield 'data: {"type":"token","data":"hello back"}'
                    yield ""
                    yield 'data: {"type":"completed","data":{"status":"completed"}}'
                    yield ""

                def close(self):
                    captured["closed"] = True

            return Response()

    fake_client = SimpleNamespace(conversations=FakeConversations())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(conversations, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", "pod-1", "chat", "triage", "hello"])

    assert result.exit_code == 0, result.stdout
    assert captured["create"] == ("pod-1", {"agent_name": "triage", "title": None})
    assert captured["send"] == ("pod-1", "conversation-1", "hello", True)
    assert captured["closed"] is True
    assert "Lemma chat" in result.stdout
    assert "You" in result.stdout
    assert "hello back" in result.stdout
    assert "completed" in result.stdout


def test_chat_can_use_default_pod_agent_with_message_option(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConversations:
        def create(self, pod_id, payload):
            captured["create"] = (pod_id, payload)
            return {"id": "conversation-1"}

        def send_message(self, pod_id, conversation_id, *, content, stream):
            captured["send"] = (pod_id, conversation_id, content, stream)

            class Response:
                def iter_lines(self, decode_unicode=True):
                    yield 'data: {"type":"token","data":"default reply"}'
                    yield ""

                def close(self):
                    captured["closed"] = True

            return Response()

    fake_client = SimpleNamespace(conversations=FakeConversations())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(conversations, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", "pod-1", "chat", "--message", "hello"])

    assert result.exit_code == 0, result.stdout
    assert captured["create"] == ("pod-1", {"agent_name": None, "title": None})
    assert captured["send"] == ("pod-1", "conversation-1", "hello", True)
    assert "default pod agent" in result.stdout
    assert "default reply" in result.stdout


def test_conversation_stream_attaches_to_sse(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConversations:
        def stream(self, pod_id, conversation_id, *, agent_run_id):
            captured["stream"] = (pod_id, conversation_id, agent_run_id)

            class Response:
                def iter_lines(self, decode_unicode=True):
                    yield 'data: {"type":"token","data":"attached"}'
                    yield ""

                def close(self):
                    captured["closed"] = True

            return Response()

    fake_client = SimpleNamespace(conversations=FakeConversations())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(conversations, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "conversations",
            "stream",
            "conversation-1",
            "--agent-run-id",
            "run-1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["stream"] == ("pod-1", "conversation-1", "run-1")
    assert captured["closed"] is True
    assert "attached" in result.stdout


def test_apps_init_scaffolds_project_without_network(tmp_path):
    target = tmp_path / "ops-app"
    template = write_app_template_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "--base-url",
            "https://api.lemma.work",
            "--auth-url",
            "https://lemma.work/auth",
            "apps",
            "init",
            str(target),
            "--agent",
            "triage",
            "--members",
            "--no-install",
            "--no-registry",
            "--template",
            str(template),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (target / "package.json").exists()
    assert (target / "src" / "routes" / "_authenticated" / "route.tsx").exists()
    assert (target / "components.json").exists()
    assert (target / "lemma.app.json").exists()
    assert (target / "AGENTS.md").exists()
    assert not (target / ".env").exists()
    assert not (target / ".git").exists()
    assert not (target / "node_modules").exists()
    env_local = (target / ".env.local").read_text(encoding="utf-8")
    assert 'VITE_LEMMA_API_URL="https://api.lemma.work"' in env_local
    assert 'VITE_LEMMA_AUTH_URL="https://lemma.work/auth"' in env_local
    assert 'VITE_LEMMA_POD_ID="pod-1"' in env_local
    assert 'VITE_LEMMA_APP_BASE_PATH="/"' in env_local
    assert 'VITE_LEMMA_AGENT_NAME="triage"' in env_local
    package_json = (target / "package.json").read_text(encoding="utf-8")
    assert '"name": "ops-app"' in package_json
    assert '"dev": "vite"' in package_json
    vite_config = (target / "vite.config.ts").read_text(encoding="utf-8")
    assert "VITE_LEMMA_APP_BASE_PATH" in vite_config
    assert "loadEnv" in vite_config
    assert "env.VITE_LEMMA_APP_BASE_PATH" in vite_config
    assert "process.env.VITE_LEMMA_APP_BASE_PATH" not in vite_config
    metadata = json.loads((target / "lemma.app.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "ops-app"
    assert metadata["title"] == "Ops App"
    assert metadata["stylePreset"] == "soft"
    assert metadata["legacyInitHints"]["agentName"] == "triage"
    assert metadata["legacyInitHints"]["members"] is True


def test_apps_init_rewrites_docker_host_urls_for_browser(monkeypatch, tmp_path):
    target = tmp_path / "ops-app"
    template = write_app_template_fixture(tmp_path)
    monkeypatch.setenv("LEMMA_TOKEN", "env-token")
    monkeypatch.setenv("LEMMA_BASE_URL", "http://host.docker.internal:8711")
    monkeypatch.setenv("LEMMA_AUTH_URL", "http://host.docker.internal:4173")
    monkeypatch.setenv("LEMMA_POD_ID", "pod-1")

    result = runner.invoke(
        app,
        [
            "apps",
            "init",
            str(target),
            "--no-install",
            "--no-registry",
            "--template",
            str(template),
        ],
    )

    assert result.exit_code == 0, result.stdout
    env_local = (target / ".env.local").read_text(encoding="utf-8")
    assert 'VITE_LEMMA_API_URL="http://localhost:8711"' in env_local
    assert 'VITE_LEMMA_AUTH_URL="http://localhost:4173"' in env_local
    assert 'VITE_LEMMA_POD_ID="pod-1"' in env_local
    assert "only reachable from inside Docker" in result.stdout


def test_apps_deploy_uses_context_env_and_yes(monkeypatch, tmp_path):
    source_dir = tmp_path / "app"
    source_dir.mkdir()
    (source_dir / "package.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeApps:
        pass

    class FakePods:
        def get(self, pod_id):
            return {"id": "11111111-1111-1111-1111-111111111111", "name": "Ops Pod"}

    fake_client = SimpleNamespace(apps=FakeApps(), pods=FakePods())

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(
            base_url=None,
            auth_url=None,
            config={
                "base_url": "https://api.example.test",
                "auth_url": "https://auth.example.test",
                "_runtime": {"pod": "11111111-1111-1111-1111-111111111111"},
                "defaults": {"pod_id": "11111111-1111-1111-1111-111111111111"},
            },
            output="text",
            server_source="config",
        )
        return fn(fake_client, state)

    def fake_deploy(client, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(apps, "run_with_client", fake_run_with_client)
    monkeypatch.setattr(apps, "deploy_app_bundle", fake_deploy)

    result = runner.invoke(
        app,
        [
            "apps",
            "deploy",
            "ops-app",
            "--source-dir",
            str(source_dir),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "11111111-1111-1111-1111-111111111111"
    assert "build_env" not in captured


def test_apps_deploy_accepts_single_html_file(monkeypatch, tmp_path):
    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>hi</h1>", encoding="utf-8")
    captured: dict[str, object] = {}

    fake_client = SimpleNamespace(
        apps=SimpleNamespace(),
        pods=SimpleNamespace(
            get=lambda pod_id: {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Ops",
            }
        ),
    )

    def fake_run_with_client(ctx, fn):
        state = SimpleNamespace(
            base_url=None,
            auth_url=None,
            config={
                "base_url": "https://api.example.test",
                "auth_url": "https://auth.example.test",
                "_runtime": {"pod": "11111111-1111-1111-1111-111111111111"},
                "defaults": {"pod_id": "11111111-1111-1111-1111-111111111111"},
            },
            output="text",
            server_source="config",
        )
        return fn(fake_client, state)

    def fake_deploy(client, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(apps, "run_with_client", fake_run_with_client)
    monkeypatch.setattr(apps, "deploy_app_bundle", fake_deploy)

    result = runner.invoke(
        app,
        ["apps", "deploy", "ops-app", "--source-dir", str(html_file), "--yes"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["source_dir"] == html_file
    # No-build tiers must not require/derive VITE_LEMMA_* env.
    assert "build_env" not in captured


def test_apps_init_html_writes_single_file(tmp_path):
    target = tmp_path / "spark"
    result = runner.invoke(
        app, ["apps", "init", str(target), "--html", "--title", "Spark"]
    )
    assert result.exit_code == 0, result.stdout
    index = target / "index.html"
    assert index.exists()
    body = index.read_text(encoding="utf-8")
    assert "Spark" in body
    assert "__LEMMA_CONFIG__" in body
    assert "/public/sdk/lemma-client.js" in body
    # No Vite project artifacts in HTML mode.
    assert not (target / "package.json").exists()


def test_connectors_list_shortcut_dispatches_connector_list(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def list_connectors(self, *, limit):
            captured["limit"] = limit
            return {"items": [{"id": "gmail"}]}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["connectors", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"limit": 5}
    assert "gmail" in result.stdout


def test_connectors_accounts_list_filters_by_connector(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def list_accounts(self, *, organization_id, connector_id, limit):
            captured["organization_id"] = organization_id
            captured["connector_id"] = connector_id
            captured["limit"] = limit
            return {"items": [{"id": "account-1", "connector_id": connector_id}]}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "accounts",
            "list",
            "--app",
            "gmail",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "organization_id": "org-1",
        "connector_id": "gmail",
        "limit": 3,
    }


def test_connectors_connect_request_create(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def create_connect_request(
            self, connector, *, organization_id, auth_config_id
        ):
            captured["connector"] = connector
            captured["organization_id"] = organization_id
            captured["auth_config_id"] = auth_config_id
            return {"authorization_url": "https://example.test/oauth"}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--org", "org-1", "connectors", "connect-requests", "create", "gmail"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "connector": "gmail",
        "organization_id": "org-1",
        "auth_config_id": None,
    }
    assert "Authorization Url" in result.stdout

    json_result = runner.invoke(
        app,
        [
            "--json",
            "--org",
            "org-1",
            "connectors",
            "connect-requests",
            "create",
            "gmail",
        ],
    )
    assert json_result.exit_code == 0, json_result.stdout
    assert "authorization_url" in json_result.stdout


def test_connectors_operations_surface_has_search_not_duplicate_verbs():
    result = runner.invoke(app, ["connectors", "operations", "--help"])

    assert result.exit_code == 0, result.stdout
    assert "list" in result.stdout
    assert "get" in result.stdout
    assert "search" in result.stdout
    assert "details" in result.stdout
    assert "execute" in result.stdout
    assert "discover" not in result.stdout


def test_connectors_operations_search_accepts_positional_query(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def search_operations(self, auth_config, *, organization_id, query, limit):
            captured["auth_config"] = auth_config
            captured["organization_id"] = organization_id
            captured["query"] = query
            captured["limit"] = limit
            return {
                "items": [
                    {"name": "EXCEL_CREATE_WORKBOOK", "relevance_score": 1.0},
                    {"name": "EXCEL_GET_SESSION", "relevance_score": 0.7},
                ]
            }

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "operations",
            "search",
            "excel",
            "create workbook",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "excel",
        "organization_id": "org-1",
        "query": "create workbook",
        "limit": 5,
    }
    assert "EXCEL_CREATE_WORKBOOK" in result.stdout
    assert "EXCEL_GET_SESSION" in result.stdout


def test_connectors_operations_list_uses_auth_config(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def search_operations(self, auth_config, *, organization_id, query, limit):
            captured["auth_config"] = auth_config
            captured["organization_id"] = organization_id
            captured["query"] = query
            captured["limit"] = limit
            return {"items": [{"name": "OUTLOOK_LIST_MESSAGES"}]}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "operations",
            "list",
            "outlook",
            "--limit",
            "25",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "outlook",
        "organization_id": "org-1",
        "query": None,
        "limit": 25,
    }
    assert "OUTLOOK_LIST_MESSAGES" in result.stdout


def test_connectors_operations_details_accepts_batch(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def get_operation_details_batch(
            self,
            auth_config,
            *,
            organization_id,
            operation_names,
        ):
            captured["auth_config"] = auth_config
            captured["organization_id"] = organization_id
            captured["operation_names"] = operation_names
            return {"items": [{"name": name} for name in operation_names]}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "operations",
            "details",
            "gmail",
            "messages_list",
            "messages_get",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "gmail",
        "organization_id": "org-1",
        "operation_names": ["messages_list", "messages_get"],
    }


def test_connectors_operations_get_dispatches_single_detail(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def get_operation_details(
            self,
            auth_config,
            operation,
            *,
            organization_id,
        ):
            captured["auth_config"] = auth_config
            captured["operation"] = operation
            captured["organization_id"] = organization_id
            return {"name": operation}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "operations",
            "get",
            "gmail",
            "messages_get",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "gmail",
        "operation": "messages_get",
        "organization_id": "org-1",
    }


def test_connectors_operations_execute_unwraps_payload_and_account(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def execute_operation(
            self,
            auth_config,
            operation,
            *,
            organization_id,
            payload,
            account_id,
        ):
            captured["auth_config"] = auth_config
            captured["operation"] = operation
            captured["organization_id"] = organization_id
            captured["payload"] = payload
            captured["account_id"] = account_id
            return {"ok": True}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "operations",
            "execute",
            "gmail",
            "messages_send",
            "--data",
            json.dumps({"payload": {"to": "a@example.com"}, "account_id": "acct-1"}),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "gmail",
        "operation": "messages_send",
        "organization_id": "org-1",
        "payload": {"to": "a@example.com"},
        "account_id": "acct-1",
    }


def test_connectors_auth_config_create_uses_selected_org(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def create_auth_config(self, **kwargs):
            captured.update(kwargs)
            return {"id": "auth-config-1", "name": kwargs["name"]}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "auth-configs",
            "create",
            "slack",
            "--name",
            "workspace-slack",
            "--data",
            json.dumps(
                {"oauth2_credentials": {"client_id": "id", "client_secret": "secret"}}
            ),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "organization_id": "org-1",
        "connector_id": "slack",
        "name": "workspace-slack",
        "provider": "LEMMA",
        "config_source": "SYSTEM_DEFAULT",
        "credential_config": {
            "oauth2_credentials": {"client_id": "id", "client_secret": "secret"}
        },
    }


def test_connectors_account_create_forwards_credentials(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConnectors:
        def create_account(self, **kwargs):
            captured.update(kwargs)
            return {"id": "account-1"}

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "accounts",
            "create",
            "--auth-config",
            "telegram",
            "--data",
            json.dumps({"bot_token": "token"}),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["organization_id"] == "org-1"
    assert captured["auth_config_name"] == "telegram"
    assert captured["credentials"] == {"bot_token": "token"}


def test_connectors_triggers_list_passes_auth_config(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTriggers:
        def list(self, auth_config, *, search, limit):
            captured["auth_config"] = auth_config
            captured["search"] = search
            captured["limit"] = limit
            return {
                "items": [
                    {
                        "id": "outlook:composio:new_message",
                        "connector_id": "outlook",
                        "provider": "COMPOSIO",
                    }
                ]
            }

    class FakeConnectors:
        triggers = FakeTriggers()

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "triggers",
            "list",
            "work-outlook",
            "--query",
            "message",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "work-outlook",
        "search": "message",
        "limit": 5,
    }
    assert "outlook:composio:new_message" in result.stdout


def test_connectors_triggers_list_auto_discovers_auth_config(monkeypatch):
    captured: dict[str, object] = {}

    class FakeAuthConfigs:
        def list(self, *, limit):
            return {"items": [{"name": "work-gmail", "id": "ac-1"}]}

    class FakeTriggers:
        def list(self, auth_config, *, search, limit):
            captured["auth_config"] = auth_config
            captured["search"] = search
            captured["limit"] = limit
            return {"items": [{"id": "gmail:composio:new_message"}]}

    class FakeConnectors:
        auth_configs = FakeAuthConfigs()
        triggers = FakeTriggers()

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--org", "org-1", "connectors", "triggers", "list"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "work-gmail",
        "search": None,
        "limit": 100,
    }


def test_connectors_triggers_get_fetches_trigger(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTriggers:
        def get(self, auth_config, trigger):
            captured["auth_config"] = auth_config
            captured["trigger"] = trigger
            return {"id": trigger, "connector_id": "gmail", "provider": "COMPOSIO"}

    class FakeConnectors:
        triggers = FakeTriggers()

    fake_client = SimpleNamespace(connectors=FakeConnectors())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={"_runtime": {"org": "org-1"}}))

    monkeypatch.setattr(connectors, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--org",
            "org-1",
            "connectors",
            "triggers",
            "get",
            "work-gmail",
            "gmail:composio:new_message",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "auth_config": "work-gmail",
        "trigger": "gmail:composio:new_message",
    }


def test_surfaces_upsert_uses_platform_ref(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSurfaces:
        def upsert(self, platform, request):
            captured["platform"] = platform
            captured["request"] = request
            return {"id": "surface-1", "platform": platform, **request}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(surfaces=FakeSurfaces())

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(surfaces, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "surfaces",
            "upsert",
            "slack",
            "--credential-mode",
            "system",
            "--account",
            "account-1",
            "--agent",
            "triage",
            "--allowed-domain",
            "Example.com",
            "--allowed-email",
            "VIP@Example.com",
        ],
    )

    assert result.exit_code == 0, result.stdout
    # is_enabled is omitted unless --enabled/--disabled is passed, so an upsert
    # that only edits config never silently re-enables a disabled surface.
    assert captured == {
        "pod_id": "pod-1",
        "platform": "SLACK",
        "request": {
            "default_agent_name": "triage",
            "account_id": "account-1",
            "credential_mode": "SYSTEM",
            "config": {
                "identity": {
                    "allowed_domains": ["example.com"],
                    "allowed_email_addresses": ["vip@example.com"],
                }
            },
        },
    }


def test_surfaces_channels_builds_route_payload(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSurfaces:
        # Channel routes are just config on the single upsert write.
        def upsert(self, platform, request):
            captured["platform"] = platform
            captured["request"] = request
            return {"id": "surface-1", "platform": platform, **request}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(surfaces=FakeSurfaces())

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(surfaces, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "surfaces",
            "channels",
            "slack",
            "--channel-id",
            "C123",
            "--channel-name",
            "general",
            "--agent",
            "triage",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "pod_id": "pod-1",
        "platform": "SLACK",
        "request": {
            "config": {
                "channels": [
                    {
                        "channel_id": "C123",
                        "channel_name": "general",
                        "agent_name": "triage",
                    }
                ]
            }
        },
    }


def test_surfaces_setup_uses_surface_ref(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSurfaces:
        def setup(self, surface):
            captured["surface"] = surface
            return {"platform": "SLACK", "webhook_url": "https://api.test/surfaces/webhooks/slack"}

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return SimpleNamespace(surfaces=FakeSurfaces())

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"))

    monkeypatch.setattr(surfaces, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--pod", "pod-1", "surfaces", "setup", "slack"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"pod_id": "pod-1", "surface": "slack"}


def test_profile_update_dispatches_user_profile(monkeypatch):
    from lemma_cli.cli_core.commands import profile

    captured: dict[str, object] = {}

    class FakeUser:
        def update_profile(self, request):
            captured["request"] = request
            return {"id": "user-1", **request}

    def fake_run_with_client(ctx, fn):
        return fn(SimpleNamespace(user=FakeUser()), SimpleNamespace(config={}))

    monkeypatch.setattr(profile, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        [
            "profile",
            "update",
            "--mobile-number",
            "+15551234567",
            "--telegram-username",
            "surfaceuser",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "request": {
            "mobile_number": "+15551234567",
            "telegram_username": "surfaceuser",
        },
    }


def test_tools_run_web_search_dispatches_tool_api(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTools:
        def web_search(self, *, query, max_results):
            captured["query"] = query
            captured["max_results"] = max_results
            return {"success": True}

    fake_client = SimpleNamespace(tools=FakeTools())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={}))

    monkeypatch.setattr(tools, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["tools", "run", "web-search", "--data", '{"query":"docs","max_results":2}'],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {"query": "docs", "max_results": 2}


def test_apps_init_rejects_non_empty_directory(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "file.txt").write_text("already here", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "apps",
            "init",
            str(target),
            "--no-install",
            "--no-registry",
        ],
    )

    assert result.exit_code == 1
    # Normalize whitespace: Rich wraps at the (narrow) non-TTY console width, so a
    # long tmp path can split "not empty" across a line break in CI.
    assert "not empty" in " ".join(result.stdout.split())


def test_apps_init_rejects_registry_scaffolding(tmp_path):
    target = tmp_path / "registry-app"

    result = runner.invoke(
        app,
        [
            "--pod",
            "pod-1",
            "apps",
            "init",
            str(target),
            "--no-install",
            "--registry",
        ],
    )

    assert result.exit_code == 1
    assert (
        "registry scaffolding is no longer part of `lemma apps init`" in result.stdout
    )


def test_config_servers_are_isolated(tmp_path):
    config_file = tmp_path / "config.json"

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "servers",
            "create",
            "local",
            "--base-url",
            "http://localhost:8000",
            "--auth-url",
            "http://localhost:3000/auth",
            "--use",
        ],
    )
    assert result.exit_code == 0, result.stdout

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["active_server"] == "local"
    assert saved["servers"]["local"]["base_url"] == "http://localhost:8000"
    assert saved["servers"]["local"]["auth_url"] == "http://localhost:3000/auth"
    assert saved["servers"]["local"]["defaults"] == {}
    assert saved["servers"]["default"]["defaults"] == {}


def test_server_create_without_use_keeps_active_server(tmp_path):
    config_file = tmp_path / "config.json"

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "servers",
            "create",
            "staging",
            "--base-url",
            "http://staging:8000",
        ],
    )
    assert result.exit_code == 0, result.stdout

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["active_server"] == "default"
    assert saved["servers"]["staging"]["base_url"] == "http://staging:8000"


def test_server_create_is_upsert(tmp_path):
    config_file = tmp_path / "config.json"

    for base_url in ("http://one:8000", "http://two:8000"):
        result = runner.invoke(
            app,
            [
                "--config-file",
                str(config_file),
                "servers",
                "create",
                "staging",
                "--base-url",
                base_url,
            ],
        )
        assert result.exit_code == 0, result.stdout

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["servers"]["staging"]["base_url"] == "http://two:8000"


def test_servers_add_command_is_removed(tmp_path):
    config_file = tmp_path / "config.json"
    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "servers",
            "add",
            "other",
            "--base-url",
            "http://other:8000",
        ],
    )
    assert result.exit_code != 0


def test_legacy_context_config_is_migrated(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_context": "local",
                "contexts": {
                    "default": {"defaults": {}},
                    "local": {
                        "base_url": "http://localhost:8000",
                        "token": "legacy-token",
                        "defaults": {"org_id": "org-1"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "--output",
            "json",
            "servers",
            "show",
            "--no-resolve",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["server"] == "local"
    assert payload["base_url"]["value"] == "http://localhost:8000"
    assert payload["org_id"]["value"] == "org-1"

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "servers",
            "select",
            "local",
        ],
    )
    assert result.exit_code == 0, result.stdout
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert "contexts" not in saved
    assert "active_context" not in saved
    assert saved["active_server"] == "local"
    assert saved["servers"]["local"]["token"] == "legacy-token"


def test_servers_select_without_name_opens_picker(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {
                    "default": {"defaults": {}},
                    "local": {"base_url": "http://localhost:8000", "defaults": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--config-file", str(config_file), "servers", "select"],
        input="2\n",
    )

    assert result.exit_code == 0, result.stdout
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["active_server"] == "local"


def test_bare_servers_command_lists_read_only(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {
                    "default": {"defaults": {}},
                    "local": {"base_url": "http://localhost:8000", "defaults": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--config-file", str(config_file), "servers"],
    )

    # Bare `servers` is read-only: it lists servers and never switches the active
    # one (switching is `servers select`).
    assert result.exit_code == 0, result.stdout
    assert "default" in result.stdout and "local" in result.stdout
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["active_server"] == "default"


def test_server_show_shows_sources_and_selected_values(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "local",
                "servers": {
                    "default": {"defaults": {}},
                    "local": {
                        "base_url": "http://localhost:8000",
                        "auth_url": "http://localhost:3000/auth",
                        "defaults": {
                            "org_id": "org-1",
                            "pod_id": "pod-1",
                            "conversation_id": "conversation-1",
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "--output",
            "json",
            "servers",
            "show",
            "--no-resolve",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["server"] == "local"
    assert payload["source"] == "config"
    assert payload["org_id"] == {"value": "org-1", "source": "server"}
    assert payload["pod_id"] == {"value": "pod-1", "source": "server"}
    assert payload["conversation_id"] == {
        "value": "conversation-1",
        "source": "server",
    }


def test_env_server_is_inline_and_read_only(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("LEMMA_TOKEN", "env-token")
    monkeypatch.setenv("LEMMA_BASE_URL", "http://env-api")
    monkeypatch.setenv("LEMMA_AUTH_URL", "http://env-auth")
    monkeypatch.setenv("LEMMA_ORG_ID", "env-org")
    monkeypatch.setenv("LEMMA_POD_ID", "env-pod")
    monkeypatch.setenv("LEMMA_CONVERSATION_ID", "env-conversation")

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "--output",
            "json",
            "servers",
            "show",
            "--no-resolve",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["server"] == "env"
    assert payload["source"] == "env"
    assert payload["read_only"] is True
    assert payload["path"] is None
    assert payload["base_url"] == {
        "value": "http://env-api",
        "source": "env:LEMMA_BASE_URL",
    }
    assert payload["auth_url"] == {
        "value": "http://env-auth",
        "source": "env:LEMMA_AUTH_URL",
    }
    assert payload["token"] == {"value": "env-...oken", "source": "env:LEMMA_TOKEN"}
    assert payload["org_id"] == {"value": "env-org", "source": "env:LEMMA_ORG_ID"}
    assert payload["pod_id"] == {"value": "env-pod", "source": "env:LEMMA_POD_ID"}
    assert payload["conversation_id"] == {
        "value": "env-conversation",
        "source": "env:LEMMA_CONVERSATION_ID",
    }
    assert not config_file.exists()


def test_lemma_server_env_selects_stored_server(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "active_server": "cloud",
                "servers": {
                    "cloud": {"base_url": "https://api.lemma.work", "defaults": {}},
                    "local": {"base_url": "http://localhost:8712", "defaults": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_SERVER", "local")

    result = runner.invoke(
        app,
        [
            "--config-file",
            str(config_file),
            "--output",
            "json",
            "servers",
            "show",
            "--no-resolve",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["server"] == "local"
    assert payload["base_url"]["value"] == "http://localhost:8712"


def test_init_selects_single_org_and_pod(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"

    class FakeOrganizations:
        def list(self, *, limit):
            return {"items": [{"id": "org-1", "name": "Acme"}]}

    class FakePods:
        def list_by_organization(self, org_id, *, limit):
            assert org_id == "org-1"
            return {
                "items": [{"id": "pod-1", "name": "Ops", "organization_id": "org-1"}]
            }

    fake_client = SimpleNamespace(organizations=FakeOrganizations(), pods=FakePods())

    from lemma_cli.cli_core.commands import system

    def fake_run_with_client(ctx, fn):
        from lemma_cli.cli_core.state import state_from_ctx

        return fn(fake_client, state_from_ctx(ctx))

    monkeypatch.setattr(system, "run_with_client", fake_run_with_client)

    result = runner.invoke(
        app,
        ["--config-file", str(config_file), "init"],
    )

    assert result.exit_code == 0, result.stdout
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["servers"]["default"]["defaults"]["org_id"] == "org-1"
    assert saved["servers"]["default"]["defaults"]["pod_id"] == "pod-1"


DESTRUCTIVE_COMMANDS = [
    (pods, ["pods", "delete", "pod-1"]),
    (agents, ["agents", "delete", "triage"]),
    (functions, ["functions", "delete", "adder"]),
    (schedules, ["schedules", "delete", "sched-1"]),
    (apps, ["apps", "delete", "app-1"]),
    (files, ["files", "rm", "/notes.txt"]),
    (surfaces, ["surfaces", "delete", "slack"]),
    (connectors, ["connectors", "accounts", "delete", "acct-1"]),
    (connectors, ["connectors", "auth-configs", "delete", "cfg-1"]),
]


@pytest.mark.parametrize(
    "module,args", DESTRUCTIVE_COMMANDS, ids=lambda value: " ".join(value) if isinstance(value, list) else ""
)
def test_destructive_commands_require_yes(monkeypatch, tmp_path, module, args):
    calls: list[int] = []
    monkeypatch.setattr(module, "run_with_client", lambda ctx, fn: calls.append(1))
    config_args = ["--config-file", str(tmp_path / "config.json")]

    refused = runner.invoke(app, config_args + args)
    assert refused.exit_code == 1, refused.stdout
    assert "--yes" in refused.stdout
    assert not calls

    accepted = runner.invoke(app, config_args + args + ["--yes"])
    assert accepted.exit_code == 0, accepted.stdout
    assert calls


def test_confirm_destructive_prompt_aborts_on_no(monkeypatch):
    import typer as typer_mod

    from lemma_cli.cli_core import confirm as confirm_mod
    from lemma_cli.cli_core.confirm import confirm_destructive

    monkeypatch.setattr(
        confirm_mod.sys, "stdin", SimpleNamespace(isatty=lambda: True)
    )
    monkeypatch.setattr(confirm_mod.typer, "confirm", lambda message: False)
    with pytest.raises(typer_mod.Exit):
        confirm_destructive("Delete thing?", False)


def test_confirm_destructive_yes_skips_prompt():
    from lemma_cli.cli_core.confirm import confirm_destructive

    confirm_destructive("Delete thing?", True)


def test_runtime_profiles_list_and_harnesses(monkeypatch):
    from lemma_cli.cli_core.commands import runtime as runtime_cmd

    captured: dict[str, object] = {}

    class FakeOrgRuntime:
        def profiles(self):
            captured["profiles"] = True
            return {"items": [{"id": "system:lemma", "name": "Lemma"}]}

        def create_profile(self, payload):
            captured["create"] = payload
            return {"id": "profile-1", **payload}

    class FakeRuntime:
        def harnesses(self):
            captured["harnesses"] = True
            return {"items": [{"harness_kind": "CODEX", "available": True}]}

    fake_client = SimpleNamespace(runtime=FakeRuntime(), org_runtime=FakeOrgRuntime())

    def fake_run_with_client(ctx, fn):
        return fn(fake_client, SimpleNamespace(config={}, output="json"))

    monkeypatch.setattr(runtime_cmd, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--json", "runtime", "profiles", "list"])
    assert result.exit_code == 0, result.stdout
    assert captured.get("profiles") is True

    result = runner.invoke(app, ["--json", "runtime", "harnesses"])
    assert result.exit_code == 0, result.stdout
    assert captured.get("harnesses") is True

    result = runner.invoke(
        app,
        [
            "--json",
            "runtime",
            "profiles",
            "create",
            "user_daemon",
            "--name",
            "Codex on laptop",
            "--daemon-id",
            "daemon-1",
            "--harness",
            "codex",
            "--default-model",
            "gpt-5.5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["create"] == {
        "source": "USER_DAEMON",
        "name": "Codex on laptop",
        "daemon_id": "daemon-1",
        "harness_kind": "CODEX",
        "default_model_name": "gpt-5.5",
    }


# --------------------------------------------------------------------------- #
# init-ergonomics polish: records import (bulk), doctor, schema, grant, etc.
# --------------------------------------------------------------------------- #
def _patch_run(monkeypatch, module, fake_client):
    """Route a command's run_with_client to a flat fake client (no network)."""
    def fake_run_with_client(ctx, fn):
        return fn(
            fake_client,
            SimpleNamespace(config={"_runtime": {"pod": "pod-1"}}, output="pretty"),
        )

    monkeypatch.setattr(module, "run_with_client", fake_run_with_client)


def test_records_import_uses_bulk_create_csv(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeRecords:
        def bulk_create(self, pod_id, table, records):
            captured["pod_id"] = pod_id
            captured["table"] = table
            captured["records"] = records
            return len(records)

    _patch_run(monkeypatch, data, SimpleNamespace(records=FakeRecords()))
    csv_file = tmp_path / "rows.csv"
    csv_file.write_text("title,status\nA,open\nB,done\n", encoding="utf-8")

    result = runner.invoke(app, ["--pod", "pod-1", "records", "import", "items", str(csv_file)])

    assert result.exit_code == 0, result.stdout
    assert captured["pod_id"] == "pod-1"
    assert captured["table"] == "items"
    assert captured["records"] == [
        {"title": "A", "status": "open"},
        {"title": "B", "status": "done"},
    ]
    assert "Imported 2 record(s)" in result.stdout


def test_records_import_jsonl_and_limit(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeRecords:
        def bulk_create(self, pod_id, table, records):
            captured["records"] = records
            return len(records)

    _patch_run(monkeypatch, data, SimpleNamespace(records=FakeRecords()))
    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text('{"n": 1}\n{"n": 2}\n{"n": 3}\n', encoding="utf-8")

    result = runner.invoke(
        app, ["--pod", "pod-1", "records", "import", "items", str(jsonl), "--limit", "2"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["records"] == [{"n": 1}, {"n": 2}]


def test_records_import_empty_file_does_not_call_api(monkeypatch, tmp_path):
    called = {"n": 0}

    class FakeRecords:
        def bulk_create(self, pod_id, table, records):
            called["n"] += 1
            return len(records)

    _patch_run(monkeypatch, data, SimpleNamespace(records=FakeRecords()))
    empty = tmp_path / "rows.csv"
    empty.write_text("title,status\n", encoding="utf-8")  # header only

    result = runner.invoke(app, ["--pod", "pod-1", "records", "import", "items", str(empty)])
    assert result.exit_code == 0, result.stdout
    assert "No rows to import." in result.stdout
    assert called["n"] == 0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("007", "007"),
        ("1_000", "1_000"),
        ("1.5", 1.5),
        ("42", 42),
        ("-3", -3),
        ("true", True),
        ("false", False),
        ("", None),
        ("hello", "hello"),
    ],
)
def test_coerce_csv_value(raw, expected):
    from lemma_cli.cli_app.record_io import coerce_csv_value

    assert coerce_csv_value(raw) == expected


def _doctor_client(*, tables, agents, agent_perms, surfaces=None, workflows=None, schedules=None):
    # doctor resolves the pod via pod_client(), which returns client.pod(pod_id)
    # for a catalog-capable client — so resources use hierarchical method names
    # (matching the real SDK and the `describe` test).
    class FakeTables:
        def list(self, *, limit=1000):
            return {"items": [{"name": t} for t in tables]}

    class FakeAgents:
        def list(self, *, limit=1000):
            return {"items": agents}

        def permissions(self, name):
            result = agent_perms.get(name)
            if isinstance(result, Exception):
                raise result
            return {"grants": result or []}

        def get(self, name):
            return {"name": name, "agent_runtime": {"profile_id": "p"}}

    class FakeFunctions:
        def list(self, *, limit=1000):
            return {"items": []}

        def permissions(self, name):
            return {"grants": []}

    class FakeWorkflows:
        def list(self, *, limit=1000):
            return {"items": workflows or []}

        def get(self, name):
            return {}

    class FakeSchedules:
        def list(self, *, limit=1000):
            return {"items": schedules or []}

    class FakeSurfaces:
        def list(self, *, limit=1000):
            return {"items": surfaces or []}

    pod_sdk = SimpleNamespace(
        tables=FakeTables(),
        agents=FakeAgents(),
        functions=FakeFunctions(),
        workflows=FakeWorkflows(),
        schedules=FakeSchedules(),
        surfaces=FakeSurfaces(),
    )
    # `.pod(pod_id)` so pod_client() returns this hierarchical proxy directly.
    return SimpleNamespace(pod=lambda pod_id: pod_sdk)


def test_pods_doctor_healthy(monkeypatch):
    client = _doctor_client(
        tables=["tickets"],
        agents=[{"name": "triage", "agent_runtime": {"profile_id": "p1"}}],
        agent_perms={
            "triage": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": "tickets",
                    "permission_ids": ["datastore.table.read"],
                }
            ]
        },
    )
    _patch_run(monkeypatch, pods, client)
    result = runner.invoke(app, ["--pod", "pod-1", "pods", "doctor"])
    assert result.exit_code == 0, result.stdout
    assert "healthy" in result.stdout


def test_pods_doctor_flags_missing_table_grant(monkeypatch):
    client = _doctor_client(
        tables=[],  # no tables exist
        agents=[{"name": "triage", "agent_runtime": {"profile_id": "p1"}}],
        agent_perms={
            "triage": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": "ghost",
                    "permission_ids": ["datastore.table.read"],
                }
            ]
        },
    )
    _patch_run(monkeypatch, pods, client)
    result = runner.invoke(app, ["--pod", "pod-1", "pods", "doctor"])
    assert result.exit_code == 1
    assert "ghost" in result.stdout
    assert "does not exist" in result.stdout


def test_pods_doctor_warns_on_failed_permission_check(monkeypatch):
    client = _doctor_client(
        tables=["tickets"],
        agents=[{"name": "triage", "agent_runtime": {"profile_id": "p1"}}],
        agent_perms={"triage": RuntimeError("boom")},
    )
    _patch_run(monkeypatch, pods, client)
    result = runner.invoke(app, ["--pod", "pod-1", "pods", "doctor"])
    # a failed check is a warning, not silent success and not a hard error
    assert result.exit_code == 0, result.stdout
    assert "could not read permissions" in result.stdout


def test_resource_schema_subcommands(monkeypatch):
    agent_out = runner.invoke(app, ["agent", "schema"])
    assert agent_out.exit_code == 0, agent_out.stdout
    assert "instruction" in agent_out.stdout

    table_out = runner.invoke(app, ["table", "schema"])
    assert table_out.exit_code == 0, table_out.stdout
    assert "columns" in table_out.stdout

    # top-level still works and matches per-resource output
    top = runner.invoke(app, ["schema", "agent"])
    assert top.exit_code == 0, top.stdout
    assert "instruction" in top.stdout


def test_grant_agent_and_function_cli_edit_bundle(tmp_path):
    from lemma_cli.cli_app.pod_bundle import loads_jsonc
    from lemma_cli.cli_app.scaffold import init_resource

    init_resource("agent", "triage", root=tmp_path)
    init_resource("function", "scorer", root=tmp_path)

    a = runner.invoke(app, ["agent", "grant", "triage", "tickets:read", "--root", str(tmp_path)])
    assert a.exit_code == 0, a.stdout
    agent_json = loads_jsonc((tmp_path / "agents" / "triage" / "triage.json").read_text())
    assert any(g["resource_name"] == "tickets" for g in agent_json["permissions"]["grants"])

    f = runner.invoke(app, ["function", "grant", "scorer", "tickets:write", "--root", str(tmp_path)])
    assert f.exit_code == 0, f.stdout
    fn_json = loads_jsonc((tmp_path / "functions" / "scorer" / "scorer.json").read_text())
    assert any(g["resource_name"] == "tickets" for g in fn_json["permissions"]["grants"])


def test_grant_print_does_not_write(tmp_path):
    from lemma_cli.cli_app.scaffold import init_resource

    init_resource("agent", "triage", root=tmp_path)
    before = (tmp_path / "agents" / "triage" / "triage.json").read_text()
    result = runner.invoke(
        app, ["agent", "grant", "triage", "tickets:read", "--print", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert '"grants"' in result.stdout
    assert (tmp_path / "agents" / "triage" / "triage.json").read_text() == before


def test_agents_create_missing_field_message(monkeypatch):
    from contextlib import contextmanager

    from lemma_cli.cli_core import state as state_mod

    @contextmanager
    def fake_session(state):
        # build_request raises before .create is invoked; the agents.create stub
        # only needs to exist so attribute access on the flat proxy succeeds.
        yield SimpleNamespace(agents=SimpleNamespace(create=lambda pod_id, req: req))

    monkeypatch.setattr(state_mod, "client_session", fake_session)
    result = runner.invoke(app, ["--pod", "pod-1", "agents", "create", "--data", '{"name": "x"}'])
    assert result.exit_code != 0
    assert "Missing required field: instruction." in result.stdout


def test_pod_create_with_starter_scaffolds_before_api(monkeypatch, tmp_path):
    calls = []

    def fake_run(ctx, fn):
        calls.append(fn)
        return {"id": "pod_x"}

    monkeypatch.setattr(pods, "run_with_client", fake_run)
    # target already exists -> init_pod fails before any API call
    target = tmp_path / "demo"
    target.mkdir()
    (target / "pod.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app, ["pods", "create", "demo", "--with-starter", "--dir", str(target)]
    )
    assert result.exit_code != 0
    assert calls == []  # no pod created because scaffolding failed first


def test_pod_create_with_starter_happy(monkeypatch, tmp_path):
    seq = iter([{"id": "pod_x"}, {"ok": True}])

    def fake_run(ctx, fn):
        return next(seq)

    monkeypatch.setattr(pods, "run_with_client", fake_run)
    target = tmp_path / "demo"

    result = runner.invoke(app, ["pods", "create", "demo", "--dir", str(target)])
    assert result.exit_code == 0, result.stdout
    assert (target / "pod.json").exists()
    assert (target / "agents" / "hello" / "hello.json").exists()
    assert "imported into pod pod_x" in result.stdout


def test_normalize_datastore_operations_all():
    from lemma_cli.cli_core.commands.schedules import (
        _DATASTORE_OPERATIONS,
        _normalize_datastore_operations,
    )

    assert set(_normalize_datastore_operations(["all"])) == set(_DATASTORE_OPERATIONS)
    assert set(_normalize_datastore_operations(["*"])) == set(_DATASTORE_OPERATIONS)
    assert _normalize_datastore_operations(["insert", "update"]) == ["INSERT", "UPDATE"]


def test_schedule_create_accepts_on_all(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSchedules:
        def create(self, pod_id, request):
            captured["request"] = request
            return {"ok": True}

    _patch_run(monkeypatch, schedules, SimpleNamespace(schedules=FakeSchedules()))
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "schedules", "create", "--workflow", "wf", "--datastore", "t", "--on", "all"],
    )
    assert result.exit_code == 0, result.stdout
    from lemma_cli.cli_core.io import to_plain

    config = to_plain(captured["request"]).get("config") or {}
    assert set(config.get("operations") or []) == {"INSERT", "UPDATE", "DELETE"}


def test_tables_add_column_via_flags(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTables:
        def add_column(self, pod_id, table, request):
            captured["table"] = table
            captured["request"] = request
            return {"ok": True}

    _patch_run(monkeypatch, data, SimpleNamespace(tables=FakeTables()))
    result = runner.invoke(
        app,
        ["--pod", "pod-1", "tables", "add-column", "tickets", "status",
         "--type", "enum", "--option", "open", "--option", "done"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["table"] == "tickets"
    from lemma_cli.cli_core.io import to_plain

    column = to_plain(captured["request"])["column"]
    assert column["name"] == "status"
    assert column["type"] == "ENUM"
    assert column["options"] == ["open", "done"]


def test_tables_add_column_requires_name_without_data(monkeypatch):
    _patch_run(monkeypatch, data, SimpleNamespace(tables=SimpleNamespace()))
    result = runner.invoke(app, ["--pod", "pod-1", "tables", "add-column", "tickets"])
    assert result.exit_code != 0
    assert "column NAME" in result.output


def test_tables_drop_column_confirmation(monkeypatch):
    called = {"n": 0}

    class FakeTables:
        def remove_column(self, pod_id, table, name):
            called["n"] += 1
            return {"ok": True}

    _patch_run(monkeypatch, data, SimpleNamespace(tables=FakeTables()))
    # non-interactive without --yes refuses, never calls the API
    rejected = runner.invoke(app, ["--pod", "pod-1", "tables", "drop-column", "tickets", "old"])
    assert rejected.exit_code != 0
    assert called["n"] == 0

    confirmed = runner.invoke(
        app, ["--pod", "pod-1", "tables", "drop-column", "tickets", "old", "--yes"]
    )
    assert confirmed.exit_code == 0, confirmed.stdout
    assert called["n"] == 1
