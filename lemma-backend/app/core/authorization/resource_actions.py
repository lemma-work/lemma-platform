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
        Permissions.DATASTORE_RECORD_DELETE,
        Permissions.DATASTORE_TABLE_UPDATE,
        Permissions.DATASTORE_TABLE_DELETE,
    ),
    ResourceType.DOCUMENT: (
        Permissions.FOLDER_READ,
        Permissions.FOLDER_WRITE,
        Permissions.FOLDER_DELETE,
        Permissions.FOLDER_SHARE,
    ),
    ResourceType.FOLDER: (
        Permissions.FOLDER_READ,
        Permissions.FOLDER_WRITE,
        Permissions.FOLDER_DELETE,
        Permissions.FOLDER_SHARE,
    ),
    ResourceType.APP: (
        Permissions.APP_READ,
        Permissions.APP_UPDATE,
        Permissions.APP_PUBLISH,
        Permissions.APP_DELETE,
    ),
    # A pod connector and a pod account are grant targets for their one use
    # each: ``connector.use`` to an agent or a role, ``connector_account.use``
    # to the members an owner shares a RESTRICTED account with.
    ResourceType.CONNECTOR: (Permissions.CONNECTOR_USE,),
    ResourceType.CONNECTOR_ACCOUNT: (Permissions.CONNECTOR_ACCOUNT_USE,),
    # Only `use` is grantable: managing a machine is a role's, never a
    # workload's -- an agent that could resize or delete the machine it runs
    # on could also take it away from the pod.
    ResourceType.MACHINE: (Permissions.MACHINE_USE,),
}

# Owners get the full action set of resources they own. Schedules are owned by
# their creator (see Schedule.user_id) and default to PERSONAL visibility, so the
# owner is the only principal who can read/update/delete them — this lets any pod
# member manage the schedules they create without pod-wide schedule permissions.
OWNER_RESOURCE_ACTIONS: dict[ResourceType, tuple[str, ...]] = {
    **RESOURCE_ACTIONS,
    # Declaring a pod connector confers nothing on whoever declared it: using
    # it is the role's or the grant's business, like anyone else's.
    ResourceType.CONNECTOR: (),
    # Sharing is the owner's act and nobody else's (audit H3). It is here and
    # in no role, so a pod admin can remove an account from the pod but never
    # put someone's credential in front of anyone.
    ResourceType.CONNECTOR_ACCOUNT: (
        Permissions.CONNECTOR_ACCOUNT_USE,
        Permissions.CONNECTOR_ACCOUNT_SHARE,
    ),
}


def owner_actions_for_resource(resource_type: ResourceType) -> tuple[str, ...]:
    return OWNER_RESOURCE_ACTIONS.get(
        resource_type, RESOURCE_ACTIONS.get(resource_type, ())
    )
