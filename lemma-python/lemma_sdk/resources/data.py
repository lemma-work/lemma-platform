from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from ..openapi_client.api.query import query_execute
from ..openapi_client.api.records import (
    record_bulk_create,
    record_bulk_delete,
    record_bulk_update,
    record_create,
    record_delete,
    record_get,
    record_list,
    record_update,
)
from ..openapi_client.api.tables import (
    table_column_add,
    table_column_remove,
    table_create,
    table_delete,
    table_get,
    table_list,
    table_update,
)
from ..openapi_client.models.add_column_request import AddColumnRequest
from ..openapi_client.models.bulk_create_records_request import BulkCreateRecordsRequest
from ..openapi_client.models.bulk_delete_records_request import BulkDeleteRecordsRequest
from ..openapi_client.models.bulk_update_records_request import BulkUpdateRecordsRequest
from ..openapi_client.models.create_record_request import CreateRecordRequest
from ..openapi_client.models.create_table_request import CreateTableRequest
from ..openapi_client.models.datastore_query_request import DatastoreQueryRequest
from ..openapi_client.models.datastore_query_response import DatastoreQueryResponse
from ..openapi_client.models.record_list_response import RecordListResponse
from ..openapi_client.models.table_detail_response import TableDetailResponse
from ..openapi_client.models.table_list_response import TableListResponse
from ..openapi_client.models.update_record_request import UpdateRecordRequest
from ..openapi_client.models.update_table_request import UpdateTableRequest
from ..openapi_client.types import UNSET
from ..types import RecordData
from .base import BoundResource

RecordFilterClause = dict[str, Any]
RecordSortClause = dict[str, Any]


def _as_record(result: Any) -> RecordData:
    """Normalize a single-record API response to a plain dict.

    Record create/get/update now return the bare record object (no ``{data}``
    envelope); the generated client models are dict-like, so collapse them to a
    JSON object for ergonomic access.
    """
    if result is None:
        return {}
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return result


def _jsonify(value: Any) -> Any:
    """Recursively coerce Python values into JSON-native types.

    Record payloads frequently carry values that ``json.dumps`` can't encode on
    its own — most commonly ``ctx.user_id`` (a :class:`uuid.UUID`), but also
    ``datetime``/``date``, ``Decimal``, and enum members. Without this the create
    path raised ``Object of type UUID is not JSON serializable`` and callers had
    to sprinkle ``str(...)`` over every id field. Coerce once, here.
    """
    if isinstance(value, dict):
        return {key: _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return _jsonify(value.value)
    return value


def _serialize_record_clauses(
    clauses: list[RecordFilterClause] | list[RecordSortClause] | None,
) -> list[str] | None:
    if clauses is None:
        return None
    return [json.dumps(clause, separators=(",", ":")) for clause in clauses]


class PodTables(BoundResource):
    def list(self, *, limit: int = 100) -> TableListResponse:
        return self._call(table_list, self._pod_uuid(), limit=limit)

    def create(self, request: CreateTableRequest) -> TableDetailResponse:
        """Create a table from a :class:`CreateTableRequest`.

        The request carries typed ``columns``, a ``primary_key_column``, optional
        ``config``, and ``enable_rls`` (row-level security).

        ``enable_rls`` **defaults to True**. With RLS on, each row is owned by its
        creator: non-admin members read/update/delete only their own rows, and
        other users' rows are invisible (cross-user access returns 404). Pod
        admins (table-delete capable) see and manage every row. Use the default
        for per-user/personal data.

        Set ``enable_rls=false`` for SHARED/reference/team tables that all members
        should see and mutate. RLS only scopes *which* rows a non-admin can touch;
        it does not change the permission a write needs — writing any table still
        requires DATASTORE_RECORD_WRITE (POD_USER and above).
        """
        return self._call(table_create, self._pod_uuid(), body=request)

    def create_from_dict(self, payload: dict[str, Any]) -> TableDetailResponse:
        return self.create(CreateTableRequest.from_dict(payload))

    def get(self, name: str) -> TableDetailResponse:
        return self._call(table_get, self._pod_uuid(), name)

    def update(self, name: str, request: UpdateTableRequest) -> TableDetailResponse:
        return self._call(table_update, self._pod_uuid(), name, body=request)

    def update_from_dict(
        self, name: str, payload: dict[str, Any]
    ) -> TableDetailResponse:
        return self.update(name, UpdateTableRequest.from_dict(payload))

    def delete(self, name: str) -> None:
        self._call(table_delete, self._pod_uuid(), name)

    def add_column(self, table: str, request: AddColumnRequest) -> TableDetailResponse:
        return self._call(table_column_add, self._pod_uuid(), table, body=request)

    def remove_column(self, table: str, column: str) -> TableDetailResponse:
        return self._call(table_column_remove, self._pod_uuid(), table, column)


class PodRecords(BoundResource):
    def list(
        self,
        table: str,
        *,
        limit: int = 20,
        offset: int = 0,
        filter: list[RecordFilterClause] | None = None,
        sort: list[RecordSortClause] | None = None,
        page_token: str | None = None,
    ) -> RecordListResponse:
        serialized_filter = _serialize_record_clauses(filter)
        serialized_sort = _serialize_record_clauses(sort)
        return self._call(
            record_list,
            self._pod_uuid(),
            table,
            limit=limit,
            offset=offset,
            filter_=serialized_filter if serialized_filter is not None else UNSET,
            sort=serialized_sort if serialized_sort is not None else UNSET,
            page_token=page_token if page_token is not None else UNSET,
        )

    def create(self, table: str, data: RecordData) -> RecordData:
        """Create one record; returns the bare record object (no ``{data}`` envelope).

        Requires DATASTORE_RECORD_WRITE (POD_USER and above). On an RLS table the
        new row is owned by the caller.
        """
        return _as_record(
            self._call(
                record_create,
                self._pod_uuid(),
                table,
                body={"data": _jsonify(data)},
                body_model=CreateRecordRequest,
            )
        )

    insert = create

    def get(self, table: str, record_id: str) -> RecordData:
        """Fetch one record; returns the bare record object (no ``{data}`` envelope).

        On an RLS table a non-admin only sees their own rows; another user's row
        (or a missing id) returns 404.
        """
        return _as_record(self._call(record_get, self._pod_uuid(), table, record_id))

    def update(self, table: str, record_id: str, data: RecordData) -> RecordData:
        """Update one record; returns the bare updated record (no ``{data}`` envelope).

        Only the passed fields change. Requires DATASTORE_RECORD_WRITE (POD_USER
        and above). On an RLS table a non-admin can only update their own rows;
        another user's row returns 404.
        """
        return _as_record(
            self._call(
                record_update,
                self._pod_uuid(),
                table,
                record_id,
                body={"data": _jsonify(data)},
                body_model=UpdateRecordRequest,
            )
        )

    def delete(self, table: str, record_id: str) -> None:
        self._call(record_delete, self._pod_uuid(), table, record_id)

    def bulk_create(
        self, table: str, records: list[RecordData], *, upsert: bool = False
    ) -> int:
        """Create many records at once; returns the count affected.

        Each item is a row dict (primary keys are generated unless provided).
        With ``upsert=True`` rows that conflict on the table's primary key are
        updated instead of failing — idempotent re-seeding. Requires
        DATASTORE_RECORD_WRITE (POD_USER and above).
        """
        result = self._call(
            record_bulk_create,
            self._pod_uuid(),
            table,
            body={"records": _jsonify(records), "upsert": upsert},
            body_model=BulkCreateRecordsRequest,
        )
        return result.count if result is not None else 0

    def bulk_update(self, table: str, records: list[dict[str, Any]]) -> int:
        """Update many records at once; returns the count updated.

        Each item is a flat dict that MUST include the table's primary-key value
        plus the fields to change. Requires DATASTORE_RECORD_WRITE (POD_USER and
        above).
        """
        result = self._call(
            record_bulk_update,
            self._pod_uuid(),
            table,
            body={"records": _jsonify(records)},
            body_model=BulkUpdateRecordsRequest,
        )
        return result.count if result is not None else 0

    def bulk_delete(self, table: str, record_ids: list[str]) -> int:
        """Delete many records by primary-key value; returns the count deleted.

        Requires DATASTORE_RECORD_WRITE (POD_USER and above).
        """
        result = self._call(
            record_bulk_delete,
            self._pod_uuid(),
            table,
            body={"record_ids": record_ids},
            body_model=BulkDeleteRecordsRequest,
        )
        return result.count if result is not None else 0


class PodQueries(BoundResource):
    def run(self, query: str) -> DatastoreQueryResponse:
        """Run a read-only SQL query over the pod's tables.

        Returns a response whose ``.to_dict()`` is ``{"items": [...], "total": N}``.
        A single SELECT only — no writes. Joins, aggregates, and subqueries across
        tables are allowed, including RLS tables, whose rows are scoped to the
        caller unless they administer the table.
        """
        return self._call(
            query_execute,
            self._pod_uuid(),
            body={"query": query},
            body_model=DatastoreQueryRequest,
        )

    execute = run


class Table:
    def __init__(self, records: PodRecords, name: str) -> None:
        self._records = records
        self.name = name

    def list(self, **kwargs: Any) -> RecordListResponse:
        return self._records.list(self.name, **kwargs)

    def create(self, data: RecordData) -> RecordData:
        return self._records.create(self.name, data)

    insert = create

    def get(self, record_id: str) -> RecordData:
        return self._records.get(self.name, record_id)

    def update(self, record_id: str, data: RecordData) -> RecordData:
        return self._records.update(self.name, record_id, data)

    def delete(self, record_id: str) -> None:
        self._records.delete(self.name, record_id)

    # The batch trio exists on this facade and not only on ``PodRecords``
    # because a caller holding a table handle would otherwise have no batch
    # path at all, and the shortest way to write N rows would be a loop of
    # ``create`` -- N round trips from wherever the code runs back to the API.
    # Inside a function sandbox that is the dominant cost of the whole call:
    # a 200-row batch is one round trip against 200.

    def bulk_create(self, records: list[RecordData], *, upsert: bool = False) -> int:
        """Create many records in one round trip; returns the count affected.

        See :meth:`PodRecords.bulk_create` for the ``upsert`` semantics.
        """
        return self._records.bulk_create(self.name, records, upsert=upsert)

    def bulk_update(self, records: list[dict[str, Any]]) -> int:
        """Update many records in one round trip; returns the count updated.

        Each item must include this table's primary-key value.
        """
        return self._records.bulk_update(self.name, records)

    def bulk_delete(self, record_ids: list[str]) -> int:
        """Delete many records by primary-key value; returns the count deleted."""
        return self._records.bulk_delete(self.name, record_ids)
