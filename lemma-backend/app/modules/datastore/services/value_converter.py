from datetime import datetime, date, timezone
import json
from typing import Any, Dict, List, Union
from uuid import UUID
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _as_utc(value: datetime) -> datetime:
    """Give a zone-less datetime the one the column is stored in."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _is_container_json_text(value: str) -> bool:
    text = value.strip()
    if text[:1] not in ("{", "["):
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def json_text(value: object) -> str:
    """The JSON text a JSON column is bound with, for any JSON value.

    The driver binds a JSONB parameter as JSON *text* and encodes nothing
    itself, so a number, a boolean or a string has to be encoded here as much
    as an object or a list -- passed through, ``42`` and ``True`` failed in the
    driver and ``"hello"`` was refused by the database as malformed JSON.

    A string is stored as a JSON string, with one exception: a string that is
    an object or an array written as JSON (``'{"a": 1}'``, ``'[1, 2]'``) is
    stored as that object or array. The column always took such a string that
    way, and a client that serializes its payload before sending depends on
    it. Any other string -- ``"hello"``, ``"42"``, ``"true"`` -- is the string
    it is.
    """
    if isinstance(value, str) and _is_container_json_text(value):
        return value
    return json.dumps(value, default=_json_default)


class ValueConverter:
    @staticmethod
    def parse_datetime(value: str) -> datetime:
        """Parse a DATETIME value, always as an instant.

        A `DATETIME` column is `TIMESTAMP WITH TIME ZONE`, so a value with no
        zone is not a time -- PostgreSQL resolves it against the session's
        ``TimeZone``, which means the same string denotes different instants on
        two deployments and the value read back is not the value sent. Rejecting
        the zone-less form would break every caller that sends
        ``"2026-01-01 09:00:00"``, so it is read as UTC, stated in the column
        type's description, and the datastore engine pins the session zone to
        match.
        """
        try:
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            return _as_utc(datetime.fromisoformat(normalized))
        except ValueError:
            pass
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ]
        for fmt in formats:
            try:
                return _as_utc(datetime.strptime(value, fmt))  # noqa: DTZ007
            except ValueError:
                continue
        raise ValueError(f"Invalid datetime format: {value}")

    @staticmethod
    def parse_date(value: str) -> date:
        try:
            return datetime.fromisoformat(value.split("T")[0]).date()
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
        except ValueError:
            raise ValueError(f"Invalid date format: {value}")

    @staticmethod
    def convert_value(value: Any, column: ColumnSchema) -> Any:
        if value is None:
            return None
        col_type = column.type

        if col_type == DatastoreDataType.DATETIME:
            if isinstance(value, datetime):
                return _as_utc(value)
            if isinstance(value, str):
                return ValueConverter.parse_datetime(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to datetime")
        if col_type == DatastoreDataType.DATE:
            if isinstance(value, date):
                return value
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, str):
                return ValueConverter.parse_date(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to date")
        if col_type in {DatastoreDataType.UUID, DatastoreDataType.USER}:
            if isinstance(value, UUID):
                return value
            if isinstance(value, str):
                return UUID(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to UUID")
        if col_type in {DatastoreDataType.INTEGER, DatastoreDataType.SERIAL}:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                return int(value)
            raise ValueError("Cannot convert to integer")
        if col_type == DatastoreDataType.FLOAT:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if isinstance(value, str):
                return float(value)
            raise ValueError("Cannot convert to float")
        if col_type == DatastoreDataType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            if isinstance(value, int):
                return bool(value)
            raise ValueError("Cannot convert to boolean")
        return value

    @staticmethod
    def serialize_for_sql(value: Any, column: ColumnSchema) -> Any:
        if value is None:
            return None
        if column.type == DatastoreDataType.JSON:
            return json_text(value)
        return value

    @staticmethod
    def convert_record(
        data: Dict[str, Any],
        columns: List[ColumnSchema],
        skip_computed=True,
        skip_auto=True,
    ) -> Dict[str, Any]:
        converted = {}
        column_map = {col.name: col for col in columns}
        for key, value in data.items():
            if key not in column_map:
                converted[key] = value
                continue
            col = column_map[key]
            if skip_computed and col.computed:
                continue
            if skip_auto and col.auto:
                continue
            try:
                converted[key] = ValueConverter.convert_value(value, col)
            except ValueError as e:
                raise ValueError(f"Invalid value for column '{key}': {e}")
        return converted

    @staticmethod
    def convert_output_value(value: Any, column: ColumnSchema) -> Any:
        # Check for Decimal explicitly if we import it, or rely on type check
        if value is None:
            return None

        col_type = column.type

        if col_type == DatastoreDataType.JSON:
            # The driver has already decoded the column's JSON. A ``str`` here
            # is a JSON string, so parsing it again would turn a stored "42"
            # into 42 and "true" into True.
            return value

        # Handle Decimal from numeric/int columns if driver returns it
        from decimal import Decimal

        if isinstance(value, Decimal):
            if (
                col_type == DatastoreDataType.INTEGER
                or col_type == DatastoreDataType.SERIAL
            ):
                return int(value)
            # Default to float for other numeric types
            return float(value)

        return value

    @staticmethod
    def deserialize_record(
        row: Dict[str, Any], columns: List[ColumnSchema]
    ) -> Dict[str, Any]:
        """Convert database row values to API-friendly types."""
        converted = row.copy()
        column_map = {col.name: col for col in columns}

        for key, value in row.items():
            if key in column_map:
                converted[key] = ValueConverter.convert_output_value(
                    value, column_map[key]
                )

        return converted

    @staticmethod
    def parse_primary_key(value: Any, pk_column: ColumnSchema) -> Union[int, str, UUID]:
        if pk_column.type == DatastoreDataType.INTEGER:
            return int(value)
        if pk_column.type in {DatastoreDataType.UUID, DatastoreDataType.USER}:
            return value if isinstance(value, UUID) else UUID(str(value))
        if pk_column.type == DatastoreDataType.SERIAL:
            return int(value)
        return value
