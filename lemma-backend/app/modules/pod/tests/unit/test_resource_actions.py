from __future__ import annotations

from app.core.authorization.context import ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.sql_actions import (
    actions_for_resource,
    read_action_for_resource,
)


def test_resource_action_mapping_is_stable_for_agent():
    assert RESOURCE_ACTIONS[ResourceType.AGENT] == (
        Permissions.AGENT_READ,
        Permissions.AGENT_EXECUTE,
        Permissions.AGENT_UPDATE,
        Permissions.AGENT_DELETE,
    )
    assert (
        actions_for_resource(ResourceType.AGENT) == RESOURCE_ACTIONS[ResourceType.AGENT]
    )


def test_read_action_for_resource_uses_resource_mapping():
    assert read_action_for_resource(ResourceType.FUNCTION) == Permissions.FUNCTION_READ
    assert read_action_for_resource(ResourceType.APP) == Permissions.APP_READ
    assert read_action_for_resource(ResourceType.DATASTORE_TABLE) == (
        Permissions.DATASTORE_TABLE_READ
    )
