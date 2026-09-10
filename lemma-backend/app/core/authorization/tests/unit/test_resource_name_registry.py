"""The resource-name table is assembled from modules, and must not lose an entry.

It used to be a literal dict in `app/core/authorization/resource_names.py`, which
is why core imported eight modules' ORM models: core decided what a resource
name is and asked each module how to spell it. Modules declare their own now,
through `LemmaModule.resource_names`, and `configure_resource_names` collects
them at assembly.

The risk that trade introduces is silence. A module that stops declaring, or an
assembly that never runs, leaves the table short -- and a missing entry does not
raise, it makes every grant naming that resource type resolve to `None`, which
reads exactly like "there is no resource by that name". These tests are the
thing that notices.
"""

from __future__ import annotations

import pytest

from app.core.authorization.context import ResourceType
from app.core.authorization.resource_names import (
    _NAME_REGISTRY,
    _require_registry,
    declare_resource_names,
)
from app.core.registry.assembly import configure_resource_names
from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit

#: Transcribed from the literal dict this replaced, at the commit before the
#: move. A type leaving this set is a grant that stops resolving, so it is
#: spelled out rather than derived from the thing under test.
EXPECTED_TYPES = {
    ResourceType.AGENT,
    ResourceType.FUNCTION,
    ResourceType.WORKFLOW,
    ResourceType.APP,
    ResourceType.DATASTORE_TABLE,
    ResourceType.SCHEDULE,
    ResourceType.FOLDER,
    ResourceType.DOCUMENT,
}


@pytest.fixture
def assembled() -> dict:
    configure_resource_names(OSS_MODULES)
    return _NAME_REGISTRY


def test_assembly_declares_every_type_the_literal_table_had(assembled: dict) -> None:
    assert set(assembled) == EXPECTED_TYPES


def test_every_declared_table_names_three_real_columns(assembled: dict) -> None:
    for resource_type, table in assembled.items():
        for label, column in (
            ("id", table.id_column),
            ("pod", table.pod_column),
            ("name", table.name_column),
        ):
            # An `InstrumentedAttribute` carries the column it maps to; anything
            # else here would fail later, inside a query, as a SQLAlchemy error
            # about a value it cannot compile.
            assert hasattr(column, "key"), f"{resource_type}: {label} is not a column"


def test_schedule_keeps_its_internal_filter(assembled: dict) -> None:
    # The one entry with a predicate. Internal schedules are created by the
    # platform rather than named by a person, and a grant must not reach one --
    # losing this filter would silently make them name-addressable.
    assert len(assembled[ResourceType.SCHEDULE].extra_filters) == 1
    assert not assembled[ResourceType.FOLDER].extra_filters


def test_folder_and_document_address_the_same_rows(assembled: dict) -> None:
    folder = assembled[ResourceType.FOLDER]
    document = assembled[ResourceType.DOCUMENT]
    assert folder.name_column is document.name_column
    assert folder.id_column is document.id_column


def test_resolving_before_assembly_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    # The failure this design could have introduced: an empty table returns
    # `None` for every name, which is indistinguishable from "no such resource".
    monkeypatch.setattr(
        "app.core.authorization.resource_names._NAME_REGISTRY", {}, raising=True
    )
    with pytest.raises(RuntimeError, match="registry is empty"):
        _require_registry()


def test_declaring_twice_keeps_the_later_table(assembled: dict) -> None:
    # Assembly runs once per process, but a test suite calls it repeatedly.
    # Re-declaring must be idempotent rather than additive or an error.
    before = {rt: t.name_column for rt, t in assembled.items()}
    configure_resource_names(OSS_MODULES)

    # Compared by column rather than by whole table: SQLAlchemy builds a fresh
    # expression object for schedule's `is_internal` filter on every call, so
    # two equivalent declarations are not `==`. The claim that matters is that
    # re-declaring replaces rather than accumulates or drops.
    assert {rt: t.name_column for rt, t in _NAME_REGISTRY.items()} == before
    assert len(_NAME_REGISTRY[ResourceType.SCHEDULE].extra_filters) == 1


def test_a_module_may_declare_nothing(assembled: dict) -> None:
    # Most modules have no name-addressable resources; `resource_names` is
    # `None` for them and the collector must skip rather than fail.
    declaring = [m.name for m in OSS_MODULES if m.resource_names is not None]
    assert set(declaring) == {
        "agent",
        "apps",
        "datastore",
        "function",
        "schedule",
        "workflow",
    }
    declare_resource_names(())
    assert set(_NAME_REGISTRY) == EXPECTED_TYPES
