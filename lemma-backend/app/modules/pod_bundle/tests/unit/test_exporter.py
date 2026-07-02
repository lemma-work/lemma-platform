"""Exporter assembly against faked services.

Exercises the real ``BundleExporter.export`` orchestration + packing while
stubbing the DB/service wiring: the lazily-imported service builders and the
per-module response-dict adapters are monkeypatched so the test runs with no
database, and asserts the produced archive (via ``lemma_pod_bundle`` extract) has
the expected layout, resource files, and portable-variable handling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from lemma_pod_bundle import extract_bundle

import app.modules.pod_bundle.infrastructure.exporter as exporter_mod
from app.modules.pod_bundle.infrastructure.exporter import BundleExporter


# --- fakes -------------------------------------------------------------------


@dataclass
class _Named:
    name: str
    id: Any = field(default_factory=uuid4)
    data: dict[str, Any] | None = None


class _FakeTableService:
    def __init__(self, tables):
        self._tables = tables
        self.schema_manager = SimpleNamespace(get_schema_name=lambda pod_id: "pod_schema")

    async def list_tables(self, pod_id, ctx, limit=100, cursor=None):
        return list(self._tables), None

    async def get_table(self, pod_id, table_name, ctx):
        return next(t for t in self._tables if t.name == table_name)


class _FakeRecordService:
    def __init__(self, rows_by_table):
        self._rows = rows_by_table

    async def list_records(self, table_context, user_id, limit=20, offset=0):
        rows = self._rows.get(table_context.name, [])
        page = rows[offset : offset + limit]
        return [SimpleNamespace(data=r) for r in page], len(rows)


class _FakeFunctionService:
    def __init__(self, functions):
        self._functions = functions

    async def list_functions(self, pod_id, user_id, limit=100, cursor=None, ctx=None):
        return list(self._functions), None

    async def get_function_by_name(self, pod_id, name, user_id, raise_not_found=False, ctx=None):
        return next(f for f in self._functions if f.name == name)


class _FakeAgentService:
    def __init__(self, agents):
        self._agents = agents

    async def list_agents(self, pod_id, cursor=None, limit=100, requester_user_id=None, ctx=None):
        return list(self._agents), None

    async def get_agent_by_name(self, pod_id, name, requester_user_id=None, ctx=None):
        return next(a for a in self._agents if a.name == name)


class _EmptyListService:
    async def list_flows(self, pod_id, limit=100, cursor=None, requester_user_id=None, ctx=None):
        return [], None

    async def list_schedules(self, pod_id=None, limit=100, cursor=None, ctx=None):
        return [], None

    async def list_apps(self, pod_id, user_id, limit, cursor, ctx=None):
        return [], None


class _FakeTableContext:
    def __init__(self, name):
        self.name = name

    @classmethod
    def from_table_entity(cls, table, schema_name, events_enabled=False):
        return cls(table.name)


@pytest.fixture
def patched_exporter(monkeypatch):
    """Patch the exporter's lazily-imported service builders + response adapters
    so ``export`` runs with fakes and no DB."""
    tables = [_Named("leads"), _Named("accounts")]
    functions = [_Named("enrich")]
    agents = [_Named("assistant")]
    rows_by_table = {"leads": [{"id": "1", "email": "a@x.com"}, {"id": "2", "email": "b@x.com"}]}

    empty = _EmptyListService()

    # Service builders (imported lazily inside export()).
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_table_service",
        lambda uow: _FakeTableService(tables),
    )
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_record_service",
        lambda uow: _FakeRecordService(rows_by_table),
    )
    monkeypatch.setattr(
        "app.modules.datastore.services.table_context.TableContext",
        _FakeTableContext,
    )
    monkeypatch.setattr(
        "app.modules.function.api.dependencies.build_function_service",
        lambda uow: _FakeFunctionService(functions),
    )
    monkeypatch.setattr(
        "app.modules.agent.api.dependencies.get_agent_service",
        lambda uow: _FakeAgentService(agents),
    )
    monkeypatch.setattr(
        "app.modules.workflow.api.dependencies.get_flow_service",
        lambda uow: empty,
    )
    monkeypatch.setattr(
        "app.modules.schedule.api.dependencies.get_schedule_service",
        lambda uow: empty,
    )
    monkeypatch.setattr(
        "app.modules.apps.api.dependencies.build_app_service",
        lambda uow: empty,
    )

    # Pod fetch: PodRepository(uow).get(pod_id) -> object with name.
    class _FakePodRepo:
        def __init__(self, uow, message_bus=None):
            pass

        async def get(self, pod_id):
            return _Named("My CRM Pod")

    monkeypatch.setattr(
        "app.modules.pod.infrastructure.pod_repositories.PodRepository", _FakePodRepo
    )

    # Response-dict adapters: bypass pydantic response schemas, return the shape
    # the normalizers consume.
    monkeypatch.setattr(
        exporter_mod, "_pod_response_dict", lambda pod: {"name": pod.name, "description": None, "icon_url": None}
    )
    monkeypatch.setattr(
        exporter_mod,
        "_table_response_dict",
        lambda table: {
            "name": table.name,
            "columns": [{"name": "id", "type": "TEXT"}, {"name": "email", "type": "TEXT"}],
            "config": None,
            "enable_rls": True,
            "primary_key_column": "id",
            "visibility": "POD",
        },
    )
    monkeypatch.setattr(
        exporter_mod,
        "_function_response_dict",
        lambda function: {
            "name": function.name,
            "description": "enrich fn",
            "code": "# code\nprint('hi')\n",
        },
    )
    monkeypatch.setattr(
        exporter_mod,
        "_agent_response_dict",
        lambda agent: {
            "name": agent.name,
            "description": "an agent",
            "instruction": "You are helpful.",
        },
    )
    return BundleExporter()


# --- tests -------------------------------------------------------------------


async def _run_export(patched_exporter, *, with_data, include=None):
    progress: list[tuple[int, int]] = []

    async def on_progress(done, total):
        progress.append((done, total))

    filename, zip_bytes = await patched_exporter.export(
        pod_id=uuid4(),
        user_id=uuid4(),
        with_data=with_data,
        include=include,
        ctx=object(),
        uow=object(),
        on_progress=on_progress,
    )
    return filename, zip_bytes, progress


async def test_export_produces_expected_layout(patched_exporter, tmp_path):
    filename, zip_bytes, progress = await _run_export(patched_exporter, with_data=True)

    assert filename == "my-crm-pod.zip"
    root = extract_bundle(zip_bytes, tmp_path / "out")

    pod = json.loads((root / "pod.json").read_text())
    assert pod["name"] == "My CRM Pod"
    assert pod["format_version"] == 2

    # Tables present with normalized payloads.
    assert (root / "tables" / "leads" / "leads.json").is_file()
    assert (root / "tables" / "accounts" / "accounts.json").is_file()
    leads = json.loads((root / "tables" / "leads" / "leads.json").read_text())
    assert leads["name"] == "leads"
    assert leads["primary_key_column"] == "id"

    # Function code extracted to a sidecar with a $file ref.
    fn = json.loads((root / "functions" / "enrich" / "enrich.json").read_text())
    assert fn["code"] == {"$file": "code.py"}
    assert (root / "functions" / "enrich" / "code.py").read_text() == "# code\nprint('hi')\n"

    # Agent instruction extracted.
    agent = json.loads((root / "agents" / "assistant" / "assistant.json").read_text())
    assert agent["instruction"] == {"$file": "instruction.md"}
    assert (root / "agents" / "assistant" / "instruction.md").read_text() == "You are helpful."

    # Progress advanced to completion.
    assert progress[-1] == (progress[-1][1], progress[-1][1])
    assert progress[-1][1] >= 1


async def test_with_data_writes_data_csv(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=True)
    root = extract_bundle(zip_bytes, tmp_path / "out")

    data_csv = root / "tables" / "leads" / "data.csv"
    assert data_csv.is_file()
    text = data_csv.read_text()
    assert "email" in text.splitlines()[0]
    assert "a@x.com" in text
    # accounts table has no rows -> no data.csv.
    assert not (root / "tables" / "accounts" / "data.csv").exists()


async def test_without_data_skips_data_csv(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=False)
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert not (root / "tables" / "leads" / "data.csv").exists()
    # Table schema is still exported without data.
    assert (root / "tables" / "leads" / "leads.json").is_file()


async def test_include_filters_resource_types(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, include=["tables"]
    )
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert (root / "tables" / "leads" / "leads.json").is_file()
    # agents/functions excluded when include=['tables'].
    assert not (root / "functions" / "enrich").exists()
    assert not (root / "agents" / "assistant").exists()
