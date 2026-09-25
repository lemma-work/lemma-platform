"""A catalog row whose kind was removed must not fail the whole list."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.modules.connectors.infrastructure.repositories import catalog_rows

pytestmark = pytest.mark.unit


class _Entity(BaseModel):
    id: str
    kind: str

    @classmethod
    def from_row(cls, row) -> "_Entity":
        if row.kind == "package":
            return cls.model_validate({"id": row.id, "kind": 1})
        return cls(id=row.id, kind=row.kind)


def _row(row_id: str, kind: str):
    return SimpleNamespace(__tablename__="connector_triggers", id=row_id, kind=kind)


@pytest.fixture(autouse=True)
def _fresh_throttle():
    catalog_rows._REPORTED.clear()
    yield


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def warning(self, event: str, **fields) -> None:
        self.events.append({"event": event, **fields})


def test_invalid_rows_are_skipped_and_reported_once_per_table_and_kind(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(catalog_rows, "logger", recorder)
    rows = [_row("a", "http"), _row("b", "package"), _row("c", "package")]

    first = catalog_rows.convert_valid_rows(rows, _Entity.from_row)
    second = catalog_rows.convert_valid_rows(rows, _Entity.from_row)

    assert [e.id for e in first] == ["a"] == [e.id for e in second]
    events = [
        e
        for e in recorder.events
        if e["event"] == "connectors.catalog_row.invalid.skipped"
    ]
    assert len(events) == 1
    assert events[0]["table"] == "connector_triggers"
    assert events[0]["kind"] == "package"
    assert events[0]["row_id"] == "b"


async def test_async_conversion_skips_invalid_rows():
    async def convert(row):
        return _Entity.from_row(row)

    rows = [_row("a", "package"), _row("b", "http")]

    assert [e.id for e in await catalog_rows.aconvert_valid_rows(rows, convert)] == [
        "b"
    ]


def test_other_errors_still_raise():
    def boom(row):
        raise KeyError("not a validation problem")

    with pytest.raises(KeyError):
        catalog_rows.convert_valid_rows([_row("a", "http")], boom)
