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
