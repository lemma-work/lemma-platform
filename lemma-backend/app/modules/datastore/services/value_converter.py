from datetime import datetime, date
import json
from typing import Any, Dict, List, Union
from uuid import UUID
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ValueConverter:
    @staticmethod
    def parse_datetime(value: str) -> datetime:
        try:
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            return datetime.fromisoformat(normalized)
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
                return datetime.strptime(value, fmt)
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
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid date format: {value}")

    @staticmethod
    def convert_value(value: Any, column: ColumnSchema) -> Any:
        if value is None:
            return None
        col_type = column.type

        if col_type == DatastoreDataType.DATETIME:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return ValueConverter.parse_datetime(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to datetime")
        elif col_type == DatastoreDataType.DATE:
            if isinstance(value, date):
                return value
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, str):
                return ValueConverter.parse_date(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to date")
        elif col_type in {DatastoreDataType.UUID, DatastoreDataType.USER}:
            if isinstance(value, UUID):
                return value
            if isinstance(value, str):
                return UUID(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to UUID")
        elif col_type in {DatastoreDataType.INTEGER, DatastoreDataType.SERIAL}:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                return int(value)
            raise ValueError("Cannot convert to integer")
        elif col_type == DatastoreDataType.FLOAT:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if isinstance(value, str):
                return float(value)
            raise ValueError("Cannot convert to float")
        elif col_type == DatastoreDataType.BOOLEAN:
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
            if isinstance(value, (dict, list)):
                return json.dumps(value)
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
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
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
