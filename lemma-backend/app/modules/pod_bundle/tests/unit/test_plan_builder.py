"""Plan builder diff logic against a fabricated bundle + a fake pod snapshot."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.state import StepAction, StepKind
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


class FakeExisting:
    """In-memory :class:`ExistingResources` — the pod's current resources."""

    def __init__(
        self,
        *,
        tables=None,
        table_manifests=None,
        functions=None,
        agents=None,
        workflows=None,
        schedules=None,
        apps=None,
        surfaces=None,
    ):
        self._tables = set(tables or [])
        self._table_manifests = table_manifests or {}
        self._functions = set(functions or [])
        self._agents = set(agents or [])
        self._workflows = set(workflows or [])
        self._schedules = set(schedules or [])
        self._apps = set(apps or [])
        self._surfaces = set(surfaces or [])

    async def table_names(self):
        return self._tables

    async def table_manifest(self, name):
        return self._table_manifests.get(name)

    async def function_names(self):
        return self._functions

    async def agent_names(self):
        return self._agents

    async def workflow_names(self):
        return self._workflows

    async def schedule_names(self):
        return self._schedules

    async def app_names(self):
        return self._apps

    async def surface_platforms(self):
        return self._surfaces


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _table_manifest(name, columns, pk="id"):
    return {
        "name": name,
        "primary_key_column": pk,
        "columns": [{"name": c, "type": "TEXT"} for c in columns],
    }


def _build_bundle(tmp: Path, *, variables=None) -> Path:
    root = tmp / "bundle"
    _write(root / "pod.json", {"name": "CRM", "format_version": 2, "variables": variables or {}})
    return root


@pytest.fixture
def tmp(tmp_path) -> Path:
    return tmp_path


async def test_create_vs_update_classification(tmp):
    root = _build_bundle(tmp)
    _write(root / "tables" / "leads" / "leads.json", _table_manifest("leads", ["id", "name"]))
    _write(root / "agents" / "bot" / "bot.json", {"name": "bot"})
    _write(root / "functions" / "score" / "score.json", {"name": "score", "code": "x=1"})

    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name"])},
        agents=set(),
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    by_name = {(s.kind, s.name): s for s in plan.steps}
    assert by_name[(StepKind.TABLE, "leads")].action == StepAction.UPDATE
    assert by_name[(StepKind.AGENT, "bot")].action == StepAction.CREATE
    assert by_name[(StepKind.FUNCTION, "score")].action == StepAction.CREATE
    assert plan.format_version == 2
    assert plan.bundle_name == "CRM"
    # Steps are contiguously indexed.
    assert [s.index for s in plan.steps] == list(range(len(plan.steps)))


async def test_destructive_column_drop_flagged(tmp):
    root = _build_bundle(tmp)
    # Bundle table has fewer columns than the pod's live table -> a drop.
    _write(root / "tables" / "leads" / "leads.json", _table_manifest("leads", ["id", "name"]))
    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name", "score"])},
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    step = next(s for s in plan.steps if s.kind == StepKind.TABLE)
    assert step.action == StepAction.UPDATE
    assert step.destructive is True
    assert "score" in step.detail["columns_to_remove"]
    assert any("score" in w for w in plan.warnings)


async def test_non_destructive_update_when_only_adding_columns(tmp):
    root = _build_bundle(tmp)
    _write(root / "tables" / "leads" / "leads.json", _table_manifest("leads", ["id", "name", "score"]))
    existing = FakeExisting(
        tables={"leads"},
        table_manifests={"leads": _table_manifest("leads", ["id", "name"])},
    )
    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    step = next(s for s in plan.steps if s.kind == StepKind.TABLE)
    assert step.action == StepAction.UPDATE
    assert step.destructive is False
    assert "score" in step.detail["columns_to_add"]


async def test_table_data_step_emitted_after_tables(tmp):
    root = _build_bundle(tmp)
    _write(root / "tables" / "leads" / "leads.json", _table_manifest("leads", ["id"]))
    (root / "tables" / "leads" / "data.csv").write_text("id\n1\n", encoding="utf-8")

    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    kinds = [s.kind for s in plan.steps]
    assert StepKind.TABLE in kinds and StepKind.TABLE_DATA in kinds
    # table_data comes after the table create.
    assert kinds.index(StepKind.TABLE) < kinds.index(StepKind.TABLE_DATA)


async def test_variables_classified(tmp):
    root = _build_bundle(
        tmp,
        variables={
            "acct": {"type": "account", "source_value": "x"},
            "owner": {"type": "member", "source_value": "y"},
            "region": {"type": "string", "source_value": "z"},
        },
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    by_name = {v.name: v for v in plan.variables}
    assert by_name["acct"].kind == "account"
    assert by_name["acct"].required is False
    assert by_name["owner"].kind == "pod_member"
    assert by_name["region"].kind == "free"
    assert by_name["region"].required is True


async def test_agent_grants_step_deferred_after_resources(tmp):
    root = _build_bundle(tmp)
    _write(
        root / "agents" / "bot" / "bot.json",
        {"name": "bot", "permissions": {"grants": [{"resource_type": "table"}]}},
    )
    plan = await PlanBuilder(FakeExisting()).build_plan(bundle_root=root)
    kinds = [s.kind for s in plan.steps]
    assert StepKind.AGENT in kinds and StepKind.AGENT_GRANTS in kinds
    assert kinds.index(StepKind.AGENT) < kinds.index(StepKind.AGENT_GRANTS)
