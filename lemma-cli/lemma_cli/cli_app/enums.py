"""Canonical enum value tuples, sourced from the generated SDK models.

Single source of truth for the CLI's scaffolds, help text, and validation so
these lists never drift from the backend. Each value is derived from the OpenAPI-
generated enum, so adding a backend enum member surfaces here automatically.

This is a leaf module (only `lemma_sdk` enum imports) so both the bundle layer
(`cli_app.scaffold`, `cli_app.pod_bundle`) and the command layer
(`cli_core.commands.*`) can import it without a cycle. Importing a single enum
module is cheap — it does not pull in the SDK HTTP client.
"""

from __future__ import annotations

from lemma_sdk.openapi_client.models.agent_toolset import AgentToolset
from lemma_sdk.openapi_client.models.datastore_data_type import DatastoreDataType
from lemma_sdk.openapi_client.models.datastore_operation import DatastoreOperation
from lemma_sdk.openapi_client.models.resource_visibility import ResourceVisibility
from lemma_sdk.openapi_client.models.schedule_type import ScheduleType
from lemma_sdk.openapi_client.models.surface_platform import SurfacePlatform

VISIBILITY_VALUES: tuple[str, ...] = tuple(v.value for v in ResourceVisibility)
TOOLSETS: tuple[str, ...] = tuple(v.value for v in AgentToolset)
COLUMN_TYPES: tuple[str, ...] = tuple(v.value for v in DatastoreDataType)
DATASTORE_OPERATIONS: tuple[str, ...] = tuple(v.value for v in DatastoreOperation)
SCHEDULE_TYPES: tuple[str, ...] = tuple(v.value for v in ScheduleType)
SURFACE_PLATFORMS: tuple[str, ...] = tuple(v.value for v in SurfacePlatform)

# The one sentence naming the platforms, so the lazy command registry in
# `cli_core/app.py` and the `surfaces` group itself cannot disagree. Spelling it
# out is how the help came to advertise Gmail and Outlook long after those
# stopped being surfaces and became connectors — and then, once the group was
# fixed, how the registry came to contradict the group.
SURFACE_PLATFORM_HELP: str = (
    f"Agent surface commands for {', '.join(SURFACE_PLATFORMS)}."
)

# Names other databases give the column types, sent as ours. The server accepts
# them too (`DATA_TYPE_ALIASES` in the datastore module); this copy exists
# because the generated enum refuses them before a request is ever made.
COLUMN_TYPE_ALIASES: dict[str, str] = {
    "TIMESTAMP": "DATETIME",
    "TIMESTAMPTZ": "DATETIME",
    "TIMESTAMP WITH TIME ZONE": "DATETIME",
    "DATE_TIME": "DATETIME",
    "NUMBER": "FLOAT",
    "NUMERIC": "FLOAT",
    "DECIMAL": "FLOAT",
    "DOUBLE": "FLOAT",
    "DOUBLE PRECISION": "FLOAT",
    "REAL": "FLOAT",
    "INT": "INTEGER",
    "STRING": "TEXT",
    "VARCHAR": "TEXT",
    "BOOL": "BOOLEAN",
    "JSONB": "JSON",
}


def canonical_column(column: object) -> object:
    """A column payload with its ``type`` upper-cased and aliases resolved.

    An unknown type is left as written, so the request still fails naming
    every valid one.
    """
    if not isinstance(column, dict) or not isinstance(column.get("type"), str):
        return column
    text = " ".join(column["type"].strip().upper().split())
    if text in COLUMN_TYPES:
        return {**column, "type": text}
    if text in COLUMN_TYPE_ALIASES:
        return {**column, "type": COLUMN_TYPE_ALIASES[text]}
    return column


def canonical_columns(columns: object) -> object:
    """Every column of a table payload through `canonical_column`."""
    if not isinstance(columns, list):
        return columns
    return [canonical_column(column) for column in columns]
