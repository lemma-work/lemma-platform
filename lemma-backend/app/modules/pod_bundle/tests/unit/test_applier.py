"""Applier dispatch, substitution, CSV, and upsert idempotency with fakes."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.applier import (
    BundleApplier,
    StepNotApplicableError,
    _read_csv,
    _substitute,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _step(kind: StepKind, name: str, *, action=StepAction.CREATE, destructive=False) -> PlanStep:
    return PlanStep(index=0, kind=kind, name=name, action=action, destructive=destructive)


def _applier(root: Path, **kw) -> BundleApplier:
    return BundleApplier(
        uow=object(), ctx=object(), pod_id=_UUID, user_id=_UUID, bundle_root=root, **kw
    )


from uuid import uuid4  # noqa: E402

_UUID = uuid4()


def test_substitute_replaces_placeholders():
    node = {"a": "${x}", "b": ["${y}", "plain"], "c": 3}
    out = _substitute(node, {"x": "1", "y": "2"})
    assert out == {"a": "1", "b": ["2", "plain"], "c": 3}


def test_substitute_leaves_unresolved():
    assert _substitute("${missing}", {"x": "1"}) == "${missing}"


def test_read_csv_parses_rows(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("title,score\nfirst,1\nsecond,\n", encoding="utf-8")
    rows = _read_csv(p)
    assert rows == [
        {"title": "first", "score": "1"},
        {"title": "second", "score": None},
    ]


async def test_unsupported_step_raises(tmp_path):
    applier = _applier(tmp_path)
    with pytest.raises(StepNotApplicableError):
        await applier.apply_step(_step(StepKind.APP, "dashboard"))
    with pytest.raises(StepNotApplicableError):
        await applier.apply_step(_step(StepKind.SURFACE, "slack"))


class FakeTableService:
    def __init__(self):
        self.created = []
        self.added = []
        self.removed = []
        self._existing = {}

    async def get_table(self, pod_id, name, ctx):
        return self._existing.get(name)

    async def create_table(self, pod_id, name, pk, columns, config, enable_rls, *, visibility=None, ctx=None):
        self.created.append((name, [c.name for c in columns]))


async def test_table_create_calls_service(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "tables" / "leads" / "leads.json",
        {
            "name": "leads",
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "title", "type": "TEXT"},
                {"name": "created_at", "type": "TIMESTAMP", "system": True},
            ],
        },
    )
    fake = FakeTableService()
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_table_service", lambda uow: fake
    )
    await _applier(root).apply_step(_step(StepKind.TABLE, "leads"))
    # System column dropped; only user columns created.
    assert fake.created == [("leads", ["id", "title"])]


async def test_table_update_adds_new_columns_only(tmp_path, monkeypatch):
    class Existing:
        primary_key_column = "id"

        class _C:
            def __init__(self, n):
                self.name = n

        columns = [_C("id"), _C("title")]

    root = tmp_path / "bundle"
    _write(
        root / "tables" / "leads" / "leads.json",
        {
            "name": "leads",
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "title", "type": "TEXT"},
                {"name": "score", "type": "INTEGER"},
            ],
        },
    )
    fake = FakeTableService()
    fake._existing["leads"] = Existing()

    async def _add_column(pod_id, name, column, ctx):
        fake.added.append(column.name)

    fake.add_column = _add_column
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_table_service", lambda uow: fake
    )
    # Non-destructive update: adds `score`, never creates or removes.
    await _applier(root).apply_step(
        _step(StepKind.TABLE, "leads", action=StepAction.UPDATE)
    )
    assert fake.added == ["score"]
    assert fake.created == []
