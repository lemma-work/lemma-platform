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


@dataclass
class _FakeFileEntity:
    path: str
    name: str
    kind: str  # "FOLDER" | "FILE"
    visibility: str = "POD"
    description: str | None = None
    size_bytes: int = 0
    search_enabled: bool = True

    @property
    def is_folder(self) -> bool:
        return self.kind == "FOLDER"

    @property
    def is_file(self) -> bool:
        return self.kind == "FILE"


class _FakeFileService:
    """Serves a fixed file tree (by directory) + file bytes for the with_files
    export path."""

    def __init__(self, by_dir: dict[str, list[_FakeFileEntity]], contents: dict[str, bytes]):
        self._by_dir = by_dir
        self._contents = contents

    async def list_files(self, pod_id, ctx, directory_path="/", limit=100, cursor=None):
        return list(self._by_dir.get(directory_path, [])), None

    async def download_file_content_by_path(self, pod_id, path, ctx):
        return None, self._contents.get(path, b"")


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


class _FakeAppService:
    """App service that can return an app plus its stored source/dist archives so
    the exporter's asset-download path is exercised without object storage."""

    def __init__(self, apps, *, source: bytes | None = None, dist: bytes | None = None):
        self._apps = apps
        self._source = source
        self._dist = dist

    async def list_apps(self, pod_id, user_id, limit, cursor, ctx=None):
        return list(self._apps), None

    async def get_app_by_name(self, pod_id, name, user_id, raise_not_found=False, ctx=None):
        return next(a for a in self._apps if a.name == name)

    async def resolve_source_archive(self, pod_id, name, user_id, ctx=None):
        from app.modules.apps.domain.errors import AppNotFoundError

        if self._source is None:
            raise AppNotFoundError(f"no source for {name}")
        app = next(a for a in self._apps if a.name == name)
        return app.id, "source/archive.zip"

    async def resolve_dist_archive(self, pod_id, name, user_id, ctx=None):
        from app.modules.apps.domain.errors import AppNotFoundError

        if self._dist is None:
            raise AppNotFoundError(f"no dist for {name}")
        app = next(a for a in self._apps if a.name == name)
        return app.id, "releases/v1/dist/archive.zip"

    async def read_archive(self, app_id, archive_path):
        return self._source if "source" in archive_path else self._dist


def _zip_bytes(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


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


async def _run_export(
    patched_exporter, *, with_data, include=None, data_tables=None, with_files=False
):
    progress: list[tuple[int, int]] = []
    warnings_holder: list[list[str]] = []

    async def on_progress(done, total):
        progress.append((done, total))

    filename, zip_bytes, warnings = await patched_exporter.export(
        pod_id=uuid4(),
        user_id=uuid4(),
        with_data=with_data,
        data_tables=data_tables,
        with_files=with_files,
        include=include,
        ctx=object(),
        uow=object(),
        on_progress=on_progress,
    )
    warnings_holder.append(warnings)
    _run_export.last_warnings = warnings  # type: ignore[attr-defined]
    return filename, zip_bytes, progress


async def test_export_produces_expected_layout(patched_exporter, tmp_path):
    filename, zip_bytes, progress = await _run_export(patched_exporter, with_data=True)

    assert filename == "my-crm-pod.zip"
    root = extract_bundle(zip_bytes, tmp_path / "out")

    pod = json.loads((root / "pod.json").read_text())
    assert pod["name"] == "My CRM Pod"
    assert pod["format_version"] == 3

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


async def test_data_tables_seeds_only_named_table(patched_exporter, tmp_path):
    # with_data off, but leads named explicitly → leads.data.csv is written.
    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, data_tables=["leads"]
    )
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert (root / "tables" / "leads" / "data.csv").is_file()
    assert "a@x.com" in (root / "tables" / "leads" / "data.csv").read_text()


async def test_data_tables_leaves_unnamed_row_bearing_table_unseeded(
    patched_exporter, tmp_path
):
    # Only 'accounts' requested → leads (which HAS rows) is NOT seeded. Proves the
    # selection is per-table, not all-or-nothing.
    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, data_tables=["accounts"]
    )
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert not (root / "tables" / "leads" / "data.csv").exists()
    # Both schemas are still exported.
    assert (root / "tables" / "leads" / "leads.json").is_file()
    assert (root / "tables" / "accounts" / "accounts.json").is_file()


async def test_data_tables_unknown_name_warns(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, data_tables=["ghost"]
    )
    warnings = _run_export.last_warnings  # type: ignore[attr-defined]
    assert any("ghost" in w and "not found" in w for w in warnings)
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert not (root / "tables" / "leads" / "data.csv").exists()


async def test_table_data_byte_budget_truncates_rows(
    patched_exporter, tmp_path, monkeypatch
):
    # leads has 2 rows; a per-item byte cap that fits only header + 1 row →
    # data.csv is trimmed to 1 row with a truncation warning.
    monkeypatch.setattr(
        exporter_mod.pod_bundle_settings, "pod_bundle_export_max_file_bytes", 22
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=True)
    warnings = _run_export.last_warnings  # type: ignore[attr-defined]
    root = extract_bundle(zip_bytes, tmp_path / "out")

    lines = (root / "tables" / "leads" / "data.csv").read_text().splitlines()
    assert len(lines) == 2  # header + 1 row
    assert any("truncated to 1 of 2 rows" in w and "leads" in w for w in warnings)


async def test_with_files_exports_tree_bytes_and_manifest(
    patched_exporter, tmp_path, monkeypatch
):
    folder = _FakeFileEntity(path="/docs", name="docs", kind="FOLDER", description="d")
    doc = _FakeFileEntity(
        path="/docs/guide.md", name="guide.md", kind="FILE", size_bytes=5
    )
    private = _FakeFileEntity(
        path="/secret.txt", name="secret.txt", kind="FILE", visibility="PRIVATE", size_bytes=3
    )
    by_dir = {"/": [folder, private], "/docs": [doc]}
    contents = {"/docs/guide.md": b"hello", "/secret.txt": b"no!"}
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_file_service",
        lambda uow: _FakeFileService(by_dir, contents),
    )

    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, with_files=True
    )
    root = extract_bundle(zip_bytes, tmp_path / "out")

    assert (root / "files" / "docs" / ".folder.json").is_file()
    assert (root / "files" / "docs" / "guide.md").read_bytes() == b"hello"
    # PRIVATE files never travel.
    assert not (root / "files" / "secret.txt").exists()
    manifest = json.loads((root / "files" / ".files.json").read_text())
    assert [e["path"] for e in manifest["files"]] == ["/docs/guide.md"]


async def test_without_with_files_writes_no_file_bytes(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=False)
    root = extract_bundle(zip_bytes, tmp_path / "out")
    # files/ dir exists for layout parity but carries no manifest/content.
    assert not (root / "files" / ".files.json").exists()


async def test_per_table_record_cap_truncates_with_warning(patched_exporter, tmp_path, monkeypatch):
    # leads has 2 rows; cap at 1 → data.csv has 1 row + a truncation warning.
    monkeypatch.setattr(
        exporter_mod.pod_bundle_settings, "pod_bundle_export_max_records_per_table", 1
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=True)
    warnings = _run_export.last_warnings  # type: ignore[attr-defined]
    root = extract_bundle(zip_bytes, tmp_path / "out")

    data_rows = (root / "tables" / "leads" / "data.csv").read_text().splitlines()
    assert len(data_rows) == 2  # header + 1 row
    assert any("truncated to 1 of 2 rows" in w and "leads" in w for w in warnings)


async def test_overall_record_budget_makes_later_tables_schema_only(
    patched_exporter, tmp_path, monkeypatch
):
    # Total budget = 2: 'accounts' (sorted first) has no rows, 'leads' has 2 →
    # leads still fits. Set budget to 0 so ALL table data is omitted.
    monkeypatch.setattr(
        exporter_mod.pod_bundle_settings, "pod_bundle_export_max_records_total", 0
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=True)
    warnings = _run_export.last_warnings  # type: ignore[attr-defined]
    root = extract_bundle(zip_bytes, tmp_path / "out")

    assert not (root / "tables" / "leads" / "data.csv").exists()
    assert (root / "tables" / "leads" / "leads.json").is_file()  # schema still exported
    assert any("budget reached" in w and "leads" in w for w in warnings)


def test_byte_budget_helper():
    warnings: list[str] = []
    budget = exporter_mod._ByteBudget(per_file=100, total=150, warnings=warnings)
    assert budget.allow(name="a", size=80) is True
    assert budget.allow(name="big", size=200) is False  # over per-item
    assert budget.allow(name="b", size=80) is False  # over remaining (150-80=70)
    assert any("big" in w and "per-item" in w for w in warnings)
    assert any("budget reached" in w and "'b'" in w for w in warnings)


async def test_include_filters_resource_types(patched_exporter, tmp_path):
    _filename, zip_bytes, _progress = await _run_export(
        patched_exporter, with_data=False, include=["tables"]
    )
    root = extract_bundle(zip_bytes, tmp_path / "out")
    assert (root / "tables" / "leads" / "leads.json").is_file()
    # agents/functions excluded when include=['tables'].
    assert not (root / "functions" / "enrich").exists()
    assert not (root / "agents" / "assistant").exists()


async def test_app_source_exported_and_slug_tokenized(
    patched_exporter, tmp_path, monkeypatch
):
    app = _Named("dashboard")
    source = _zip_bytes({"index.html": "<h1>hi</h1>", "package.json": "{}"})
    monkeypatch.setattr(
        "app.modules.apps.api.dependencies.build_app_service",
        lambda uow: _FakeAppService([app], source=source),
    )
    monkeypatch.setattr(
        exporter_mod,
        "_app_response_dict",
        lambda a: {
            "name": a.name,
            "public_slug": "dashboard",
            "description": None,
            "visibility": "POD",
        },
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=False)
    root = extract_bundle(zip_bytes, tmp_path / "out")

    # Source archive extracted into a git-friendly tree; no dist fallback written.
    assert (root / "apps" / "dashboard" / "dashboard.json").is_file()
    assert (root / "apps" / "dashboard" / "source" / "index.html").read_text() == "<h1>hi</h1>"
    assert (root / "apps" / "dashboard" / "source" / "package.json").is_file()
    assert not (root / "apps" / "dashboard" / "dist.zip").exists()

    # public_slug tokenized into a variable with the original as its default.
    manifest = json.loads((root / "apps" / "dashboard" / "dashboard.json").read_text())
    assert manifest["public_slug"] == "${dashboard_slug}"
    pod = json.loads((root / "pod.json").read_text())
    assert pod["variables"]["dashboard_slug"]["type"] == "app_slug"
    assert pod["variables"]["dashboard_slug"]["default"] == "dashboard"


async def test_app_dist_fallback_when_no_source(patched_exporter, tmp_path, monkeypatch):
    app = _Named("widget")
    dist = _zip_bytes({"index.html": "<h1>widget</h1>"})
    monkeypatch.setattr(
        "app.modules.apps.api.dependencies.build_app_service",
        lambda uow: _FakeAppService([app], source=None, dist=dist),
    )
    monkeypatch.setattr(
        exporter_mod,
        "_app_response_dict",
        lambda a: {
            "name": a.name,
            "public_slug": "widget",
            "description": None,
            "visibility": "POD",
        },
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=False)
    root = extract_bundle(zip_bytes, tmp_path / "out")

    # No source → the built dist travels as dist.zip instead.
    assert (root / "apps" / "widget" / "dist.zip").is_file()
    assert not (root / "apps" / "widget" / "source").exists()


async def test_app_asset_over_byte_budget_is_skipped(
    patched_exporter, tmp_path, monkeypatch
):
    app = _Named("big_app")
    source = _zip_bytes({"index.html": "x" * 5000})
    monkeypatch.setattr(
        "app.modules.apps.api.dependencies.build_app_service",
        lambda uow: _FakeAppService([app], source=source),
    )
    monkeypatch.setattr(
        exporter_mod,
        "_app_response_dict",
        lambda a: {"name": a.name, "public_slug": "big-app", "visibility": "POD"},
    )
    # Per-app budget below the archive size → source is skipped with a warning,
    # but the manifest (metadata) is still exported.
    monkeypatch.setattr(
        exporter_mod.pod_bundle_settings, "pod_bundle_export_max_app_bytes", 100
    )
    _filename, zip_bytes, _progress = await _run_export(patched_exporter, with_data=False)
    warnings = _run_export.last_warnings  # type: ignore[attr-defined]
    root = extract_bundle(zip_bytes, tmp_path / "out")

    assert (root / "apps" / "big_app" / "big_app.json").is_file()
    assert not (root / "apps" / "big_app" / "source").exists()
    assert any("per-item limit" in w for w in warnings)


# --- resource-grant export ---------------------------------------------------


async def test_resource_grants_payload_serializes_grants_by_name(monkeypatch):
    """Grants export in the portable ``{"grants": [...]}`` shape the applier +
    CLI consume, keyed by resource_name (never a source-org id)."""
    from app.core.authorization.context import ResourceType

    async def _fake_list(session, *, pod_id, grantee_type, grantee_id):
        return {(ResourceType.DATASTORE_TABLE, "leads"): ["write", "read", "read"]}

    monkeypatch.setattr(
        "app.core.authorization.grants.list_grantee_resource_grants", _fake_list
    )
    out = await exporter_mod._resource_grants_payload(
        SimpleNamespace(session=object()),
        pod_id=uuid4(),
        grantee_type="AGENT",
        grantee_id=uuid4(),
    )
    assert out == {
        "grants": [
            {
                "resource_type": "datastore_table",
                "resource_name": "leads",
                "permission_ids": ["read", "write"],
            }
        ]
    }


async def test_resource_grants_payload_none_when_no_grants(monkeypatch):
    async def _fake_list(session, *, pod_id, grantee_type, grantee_id):
        return {}

    monkeypatch.setattr(
        "app.core.authorization.grants.list_grantee_resource_grants", _fake_list
    )
    out = await exporter_mod._resource_grants_payload(
        SimpleNamespace(session=object()),
        pod_id=uuid4(),
        grantee_type="FUNCTION",
        grantee_id=uuid4(),
    )
    assert out is None


async def test_resource_grants_payload_is_best_effort_on_error(monkeypatch):
    """A grant-read failure must not sink the whole export — it degrades to no
    grants for that resource."""

    async def _boom(session, *, pod_id, grantee_type, grantee_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.core.authorization.grants.list_grantee_resource_grants", _boom
    )
    out = await exporter_mod._resource_grants_payload(
        SimpleNamespace(session=object()),
        pod_id=uuid4(),
        grantee_type="AGENT",
        grantee_id=uuid4(),
    )
    assert out is None
