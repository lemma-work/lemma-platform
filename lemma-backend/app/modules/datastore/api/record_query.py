from __future__ import annotations

import json

from pydantic import ValidationError

from app.modules.datastore.api.schemas.datastore_schemas import (
    RecordFilter,
    RecordFilterOperator,
    RecordSort,
)
from app.modules.datastore.domain.errors import DatastoreValidationError

_ALLOWED_FILTER_OPS = [op.value for op in RecordFilterOperator]


def _parse_record_filter_item(item: str) -> tuple[str, str, object]:
    payload = json.loads(item)
    parsed = RecordFilter.model_validate(payload)
    return parsed.field, parsed.op.value, parsed.value


def _parse_record_sort_item(item: str) -> tuple[str, str]:
    payload = json.loads(item)
    parsed = RecordSort.model_validate(payload)
    return parsed.field, parsed.direction.value


def parse_record_filters(
    filter: list[str] | None,
) -> list[tuple[str, str, object]]:
    """Parse explicit JSON ``filter`` clauses into filter tuples."""
    parsed_filters: list[tuple[str, str, object]] = []
    if filter:
        try:
            for item in filter:
                parsed_filters.append(_parse_record_filter_item(item))
        except json.JSONDecodeError as exc:
            raise DatastoreValidationError(
                f"Invalid filter parameter: {exc}",
            ) from exc
        except ValidationError as exc:
            op_value = None
            for err in exc.errors():
                if "op" in err.get("loc", ()):
                    op_value = err.get("input")
                    break
            if op_value is not None:
                raise DatastoreValidationError(
                    f"Unsupported filter operator '{op_value}'. "
                    f"Allowed values: {', '.join(_ALLOWED_FILTER_OPS)}",
                    details={
                        "operator": op_value,
                        "allowed_operators": _ALLOWED_FILTER_OPS,
                    },
                ) from exc
            raise DatastoreValidationError(
                f"Invalid filter parameter: {exc}",
            ) from exc

    return parsed_filters


def parse_record_sorts(
    sort: list[str] | None,
) -> list[tuple[str, str]] | None:
    """Parse explicit JSON ``sort`` clauses.

    Explicit sorts must be JSON ``RecordSort`` objects.
    """
    parsed_sorts: list[tuple[str, str]] = []
    if sort:
        try:
            for item in sort:
                parsed_sorts.append(_parse_record_sort_item(item))
        except (json.JSONDecodeError, ValueError) as exc:
            raise DatastoreValidationError("Invalid sort parameter") from exc

    return parsed_sorts or None
