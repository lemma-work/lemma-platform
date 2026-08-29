"""Public datastore DTOs used by resource consumers."""

from app.modules.datastore.api.schemas.datastore_schemas import (
    RecordFilter,
    TableResponse,
)
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreConflictError,
    DatastoreFileNotFoundError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreFileDeletedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.datastore.domain.file_entities import DatastoreFileUpdateEntity
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.files.paths import (
    normalize_datastore_name,
    normalize_datastore_path,
)

__all__ = [
    # File-write events, published on the one unified datastore stream. Exported
    # so a consumer in another module can subscribe without reaching into
    # `datastore.domain` -- the agent module watches these to drop the cached
    # memory section when the file behind it changes.
    "DATASTORE_EVENTS_STREAM",
    "DatastoreFileCreatedEvent",
    "DatastoreFileDeletedEvent",
    "DatastoreFileUpdatedEvent",
    "DatastoreAccessDeniedError",
    "DatastoreConflictError",
    "DatastoreFileNotFoundError",
    # Exported so a consumer asking "does this table exist" can catch the one
    # error that means "no" instead of everything. `get_table` checks
    # authorization *after* the lookup, so a broad catch there turns a denial
    # into "absent" -- and the pod bundle applier answers that by creating a
    # second table.
    "DatastoreTableNotFoundError",
    "DatastoreFileUpdateEntity",
    "RecordFilter",
    "ColumnSchema",
    "TableResponse",
    "TableContext",
    "normalize_datastore_name",
    "normalize_datastore_path",
]
