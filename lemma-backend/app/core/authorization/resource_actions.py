"""Resource action mappings used by authorization projections."""

from __future__ import annotations

from app.core.authorization.context import ResourceType
from app.core.authorization.permissions import Permissions


RESOURCE_ACTIONS: dict[ResourceType, tuple[str, ...]] = {
    ResourceType.AGENT: (
        Permissions.AGENT_READ,
        Permissions.AGENT_EXECUTE,
        Permissions.AGENT_UPDATE,
        Permissions.AGENT_DELETE,
    ),
    ResourceType.FUNCTION: (
        Permissions.FUNCTION_READ,
        Permissions.FUNCTION_EXECUTE,
        Permissions.FUNCTION_UPDATE,
        Permissions.FUNCTION_DELETE,
    ),
    ResourceType.WORKFLOW: (
        Permissions.WORKFLOW_READ,
        Permissions.WORKFLOW_EXECUTE,
        Permissions.WORKFLOW_UPDATE,
        Permissions.WORKFLOW_DELETE,
    ),
    ResourceType.SCHEDULE: (
        Permissions.SCHEDULE_READ,
        Permissions.SCHEDULE_UPDATE,
        Permissions.SCHEDULE_DELETE,
    ),
    ResourceType.DATASTORE_TABLE: (
        Permissions.DATASTORE_TABLE_READ,
        Permissions.DATASTORE_RECORD_READ,
        Permissions.DATASTORE_RECORD_WRITE,
        Permissions.DATASTORE_TABLE_UPDATE,
        Permissions.DATASTORE_TABLE_DELETE,
    ),
    ResourceType.DOCUMENT: (
        Permissions.FOLDER_READ,
        Permissions.FOLDER_WRITE,
        Permissions.FOLDER_DELETE,
    ),
    ResourceType.FOLDER: (
        Permissions.FOLDER_READ,
        Permissions.FOLDER_WRITE,
        Permissions.FOLDER_DELETE,
    ),
    ResourceType.APP: (
        Permissions.APP_READ,
        Permissions.APP_UPDATE,
        Permissions.APP_PUBLISH,
        Permissions.APP_DELETE,
    ),
}

# Owners get the full action set of resources they own. Schedules are owned by
# their creator (see Schedule.user_id) and default to PERSONAL visibility, so the
# owner is the only principal who can read/update/delete them — this lets any pod
# member manage the schedules they create without pod-wide schedule permissions.
OWNER_RESOURCE_ACTIONS: dict[ResourceType, tuple[str, ...]] = {
    **RESOURCE_ACTIONS,
}


def owner_actions_for_resource(resource_type: ResourceType) -> tuple[str, ...]:
    return OWNER_RESOURCE_ACTIONS.get(
        resource_type, RESOURCE_ACTIONS.get(resource_type, ())
    )
