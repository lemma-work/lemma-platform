"""A record may not carry unbounded bytes.

The rule these cover is the one that was missing when a pod put multi-megabyte
JSON in a column: every byte entering a pod as a file passed a ceiling, every
byte entering as a record cell passed none.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.record_size_policy import (
    CELL_TOO_LARGE,
    ROW_TOO_LARGE,
    size_errors,
)
from app.modules.datastore.services.record_validator import RecordValidator
from app.modules.datastore.services.table_context import TableContext


def _documents_context() -> TableContext:
    """A table shaped like the one that caused the outage: a JSON body column."""
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="documents",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT),
            ColumnSchema(name="doc", type=DatastoreDataType.JSON),
        ],
    )
    return TableContext.from_table_entity(table, "pod_test")


class TestSizePolicy:
    def test_an_oversized_cell_is_refused_and_names_the_remedy(self) -> None:
        errors, details = size_errors(
            {"doc": "x" * 300}, cell_max_bytes=100, row_max_bytes=10_000
        )

        assert details == [
            {
                "field": "doc",
                "reason": CELL_TOO_LARGE,
                "size_bytes": 300,
                "max_bytes": 100,
            }
        ]
        # The message has to teach, because this is where most authors meet the
        # rule -- not in a doc.
        assert "FILE_PATH" in errors[0]
        assert "pod file" in errors[0]

    def test_a_row_of_legal_cells_is_still_bounded(self) -> None:
        errors, details = size_errors(
            {"a": "x" * 60, "b": "x" * 60}, cell_max_bytes=100, row_max_bytes=100
        )

        assert [d["reason"] for d in details] == [ROW_TOO_LARGE]
        assert details[0]["size_bytes"] == 120
        assert "1 row" not in errors[0]

    def test_json_is_measured_encoded_not_by_repr(self) -> None:
        _, details = size_errors(
            {"doc": {"k": ["v"] * 100}}, cell_max_bytes=50, row_max_bytes=0
        )

        assert details[0]["reason"] == CELL_TOO_LARGE

    def test_bounded_scalars_cost_nothing(self) -> None:
        errors, details = size_errors(
            {"n": 5, "f": 1.5, "b": True, "nothing": None},
            cell_max_bytes=1,
            row_max_bytes=1,
        )

        assert (errors, details) == ([], [])

    def test_zero_disables_the_bound(self) -> None:
        errors, details = size_errors(
            {"doc": "x" * 10_000}, cell_max_bytes=0, row_max_bytes=0
        )

        assert (errors, details) == ([], [])

    def test_a_value_that_cannot_encode_is_left_to_the_type_validator(self) -> None:
        errors, details = size_errors(
            {"doc": object()}, cell_max_bytes=1, row_max_bytes=1
        )

        assert (errors, details) == ([], [])

    def test_refusing_a_huge_value_does_not_encode_all_of_it(self) -> None:
        """Cost is the budget, not the value: a 200MB dict must not be serialized.

        Guards the early exit in the incremental encoder. Without it this call
        allocates hundreds of megabytes to produce a number it discards.
        """
        huge = {"rows": ["x" * 1024] * 200_000}

        _, details = size_errors(huge, cell_max_bytes=1024, row_max_bytes=0)

        assert details[0]["reason"] == CELL_TOO_LARGE
        # Stopped just past the budget rather than measuring ~200MB.
        assert details[0]["size_bytes"] < 8 * 1024


class TestCreateRejectsOversize:
    def test_validate_refuses_an_oversized_cell(self) -> None:
        ctx = _documents_context()
        validator = RecordValidator(ctx)

        is_valid, errors, details = validator.validate(
            {"title": "ok", "doc": "x" * (512 * 1024)}, is_creation=True
        )

        assert is_valid is False
        assert any(d["reason"] == CELL_TOO_LARGE for d in details)

    def test_validate_accepts_an_ordinary_row(self) -> None:
        ctx = _documents_context()
        validator = RecordValidator(ctx)

        is_valid, _, details = validator.validate(
            {"title": "ok", "doc": {"summary": "short"}}, is_creation=True
        )

        assert is_valid is True
        assert details == []


class TestUpdateRejectsOversize:
    def test_update_payload_refuses_an_oversized_cell(self) -> None:
        validator = RecordValidator(_documents_context())

        with pytest.raises(DatastoreValidationError) as exc:
            validator.validate_update({"doc": "x" * (512 * 1024)})

        reasons = [d["reason"] for d in exc.value.details["errors"]]
        assert CELL_TOO_LARGE in reasons

    def test_update_payload_allows_an_ordinary_value(self) -> None:
        RecordValidator(_documents_context()).validate_update({"doc": {"a": 1}})


class TestEventPayloadIsBounded:
    """An oversized row must not be copied whole into the event stream."""

    def _coordinator(self):
        from app.modules.datastore.services.record_events import RecordEventCoordinator

        return RecordEventCoordinator(dispatcher=None)

    def _ctx(self) -> TableContext:
        ctx = _documents_context()
        ctx.events_enabled = True
        return ctx

    def test_a_large_body_is_dropped_and_flagged(self, monkeypatch) -> None:
        from app.modules.datastore.config import datastore_settings
        from app.modules.datastore.domain.events import DatastoreRecordOperation

        monkeypatch.setattr(
            datastore_settings, "datastore_event_payload_max_bytes", 1024
        )

        event = self._coordinator().build(
            self._ctx(),
            "rec-1",
            DatastoreRecordOperation.UPDATE,
            {"doc": "x" * 8192},
            uuid4(),
        )

        assert event is not None
        assert event.payload_truncated is True
        assert event.payload == {}

    def test_previous_is_measured_on_its_own(self, monkeypatch) -> None:
        from app.modules.datastore.config import datastore_settings
        from app.modules.datastore.domain.events import DatastoreRecordOperation

        monkeypatch.setattr(
            datastore_settings, "datastore_event_payload_max_bytes", 1024
        )

        event = self._coordinator().build(
            self._ctx(),
            "rec-1",
            DatastoreRecordOperation.UPDATE,
            {"title": "small"},
            uuid4(),
            changed=["doc"],
            previous={"doc": "x" * 8192},
        )

        assert event is not None
        # One large column must not strip the other's prior image, or vice versa.
        assert event.payload_truncated is False
        assert event.payload == {"title": "small"}
        assert event.previous_truncated is True
        assert event.previous is None

    def test_an_ordinary_row_is_carried_whole(self, monkeypatch) -> None:
        from app.modules.datastore.config import datastore_settings
        from app.modules.datastore.domain.events import DatastoreRecordOperation

        monkeypatch.setattr(
            datastore_settings, "datastore_event_payload_max_bytes", 32 * 1024
        )

        event = self._coordinator().build(
            self._ctx(),
            "rec-1",
            DatastoreRecordOperation.INSERT,
            {"title": "ok", "doc": {"a": 1}},
            uuid4(),
        )

        assert event is not None
        assert event.payload_truncated is False
        assert event.payload == {"title": "ok", "doc": {"a": 1}}
