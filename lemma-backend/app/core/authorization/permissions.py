"""Canonical permission registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class PermissionScope(str, Enum):
    SYSTEM = "SYSTEM"
    ORG = "ORG"
    POD = "POD"


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    id: str
    scope: PermissionScope
    resource_type: str | None
    description: str
    system_only: bool = False


class Permissions:
    ORG_READ: ClassVar[str] = "org.read"
    ORG_UPDATE: ClassVar[str] = "org.update"
    ORG_MEMBER_MANAGE: ClassVar[str] = "org.member.manage"
    ORG_INVITATION_MANAGE: ClassVar[str] = "org.invitation.manage"
    ORG_BILLING_MANAGE: ClassVar[str] = "org.billing.manage"

    POD_READ: ClassVar[str] = "pod.read"
    POD_CREATE: ClassVar[str] = "pod.create"
    POD_UPDATE: ClassVar[str] = "pod.update"
    POD_DELETE: ClassVar[str] = "pod.delete"
    POD_MEMBER_MANAGE: ClassVar[str] = "pod.member.manage"
    POD_ROLE_MANAGE: ClassVar[str] = "pod.role.manage"

    DATASTORE_TABLE_READ: ClassVar[str] = "datastore.table.read"
    DATASTORE_TABLE_CREATE: ClassVar[str] = "datastore.table.create"
    DATASTORE_TABLE_UPDATE: ClassVar[str] = "datastore.table.update"
    DATASTORE_TABLE_DELETE: ClassVar[str] = "datastore.table.delete"
    DATASTORE_RECORD_READ: ClassVar[str] = "datastore.record.read"
    DATASTORE_RECORD_WRITE: ClassVar[str] = "datastore.record.write"

    FOLDER_READ: ClassVar[str] = "folder.read"
    FOLDER_WRITE: ClassVar[str] = "folder.write"
    FOLDER_DELETE: ClassVar[str] = "folder.delete"

    APP_READ: ClassVar[str] = "app.read"
    APP_CREATE: ClassVar[str] = "app.create"
    APP_UPDATE: ClassVar[str] = "app.update"
    APP_DELETE: ClassVar[str] = "app.delete"
    APP_PUBLISH: ClassVar[str] = "app.publish"

    AGENT_READ: ClassVar[str] = "agent.read"
    AGENT_CREATE: ClassVar[str] = "agent.create"
    AGENT_UPDATE: ClassVar[str] = "agent.update"
    AGENT_DELETE: ClassVar[str] = "agent.delete"
    AGENT_EXECUTE: ClassVar[str] = "agent.execute"

    FUNCTION_READ: ClassVar[str] = "function.read"
    FUNCTION_CREATE: ClassVar[str] = "function.create"
    FUNCTION_UPDATE: ClassVar[str] = "function.update"
    FUNCTION_DELETE: ClassVar[str] = "function.delete"
    FUNCTION_EXECUTE: ClassVar[str] = "function.execute"

    WORKFLOW_READ: ClassVar[str] = "workflow.read"
    WORKFLOW_CREATE: ClassVar[str] = "workflow.create"
    WORKFLOW_UPDATE: ClassVar[str] = "workflow.update"
    WORKFLOW_DELETE: ClassVar[str] = "workflow.delete"
    WORKFLOW_EXECUTE: ClassVar[str] = "workflow.execute"

    SCHEDULE_READ: ClassVar[str] = "schedule.read"
    SCHEDULE_CREATE: ClassVar[str] = "schedule.create"
    SCHEDULE_UPDATE: ClassVar[str] = "schedule.update"
    SCHEDULE_DELETE: ClassVar[str] = "schedule.delete"

    CONVERSATION_READ: ClassVar[str] = "conversation.read"
    CONVERSATION_WRITE: ClassVar[str] = "conversation.write"

    CONNECTOR_USE: ClassVar[str] = "connector.use"
    CONNECTOR_MANAGE: ClassVar[str] = "connector.manage"
    CONNECTOR_ACCOUNT_USE: ClassVar[str] = "connector_account.use"
    CONNECTOR_ACCOUNT_MANAGE: ClassVar[str] = "connector_account.manage"
    CONNECTOR_AUTH_CONFIG_MANAGE: ClassVar[str] = "connector_auth_config.manage"


PERMISSION_DEFINITIONS: tuple[PermissionDefinition, ...] = (
    PermissionDefinition(Permissions.ORG_READ, PermissionScope.ORG, "organization", "Read organization metadata"),
    PermissionDefinition(Permissions.ORG_UPDATE, PermissionScope.ORG, "organization", "Update organization settings"),
    PermissionDefinition(Permissions.ORG_MEMBER_MANAGE, PermissionScope.ORG, "organization_member", "Manage organization members"),
    PermissionDefinition(Permissions.ORG_INVITATION_MANAGE, PermissionScope.ORG, "organization_invitation", "Manage organization invitations"),
    PermissionDefinition(Permissions.ORG_BILLING_MANAGE, PermissionScope.ORG, "billing", "Manage organization billing"),
    PermissionDefinition(Permissions.POD_READ, PermissionScope.POD, "pod", "Read pod metadata"),
    PermissionDefinition(Permissions.POD_CREATE, PermissionScope.ORG, "pod", "Create pods in an organization"),
    PermissionDefinition(Permissions.POD_UPDATE, PermissionScope.POD, "pod", "Update pod settings"),
    PermissionDefinition(Permissions.POD_DELETE, PermissionScope.POD, "pod", "Delete pods"),
    PermissionDefinition(Permissions.POD_MEMBER_MANAGE, PermissionScope.POD, "pod_member", "Manage pod members"),
    PermissionDefinition(Permissions.POD_ROLE_MANAGE, PermissionScope.POD, "role", "Manage pod roles"),
    PermissionDefinition(Permissions.DATASTORE_TABLE_READ, PermissionScope.POD, "datastore_table", "Read datastore table metadata"),
    PermissionDefinition(Permissions.DATASTORE_TABLE_CREATE, PermissionScope.POD, "datastore_table", "Create datastore tables"),
    PermissionDefinition(Permissions.DATASTORE_TABLE_UPDATE, PermissionScope.POD, "datastore_table", "Update datastore table schema and configuration"),
    PermissionDefinition(Permissions.DATASTORE_TABLE_DELETE, PermissionScope.POD, "datastore_table", "Delete datastore tables"),
    PermissionDefinition(Permissions.DATASTORE_RECORD_READ, PermissionScope.POD, "datastore_record", "Read datastore records"),
    PermissionDefinition(Permissions.DATASTORE_RECORD_WRITE, PermissionScope.POD, "datastore_record", "Write datastore records"),
    PermissionDefinition(Permissions.FOLDER_READ, PermissionScope.POD, "folder", "Read folders and files"),
    PermissionDefinition(Permissions.FOLDER_WRITE, PermissionScope.POD, "folder", "Write folders and files"),
    PermissionDefinition(Permissions.FOLDER_DELETE, PermissionScope.POD, "folder", "Delete folders and files"),
    PermissionDefinition(Permissions.APP_READ, PermissionScope.POD, "app", "Read apps"),
    PermissionDefinition(Permissions.APP_CREATE, PermissionScope.POD, "app", "Create apps"),
    PermissionDefinition(Permissions.APP_UPDATE, PermissionScope.POD, "app", "Update apps"),
    PermissionDefinition(Permissions.APP_DELETE, PermissionScope.POD, "app", "Delete apps"),
    PermissionDefinition(Permissions.APP_PUBLISH, PermissionScope.POD, "app", "Publish apps"),
    PermissionDefinition(Permissions.AGENT_READ, PermissionScope.POD, "agent", "Read agents"),
    PermissionDefinition(Permissions.AGENT_CREATE, PermissionScope.POD, "agent", "Create agents"),
    PermissionDefinition(Permissions.AGENT_UPDATE, PermissionScope.POD, "agent", "Update agents"),
    PermissionDefinition(Permissions.AGENT_DELETE, PermissionScope.POD, "agent", "Delete agents"),
    PermissionDefinition(Permissions.AGENT_EXECUTE, PermissionScope.POD, "agent", "Execute agents"),
    PermissionDefinition(Permissions.FUNCTION_READ, PermissionScope.POD, "function", "Read functions"),
    PermissionDefinition(Permissions.FUNCTION_CREATE, PermissionScope.POD, "function", "Create functions"),
    PermissionDefinition(Permissions.FUNCTION_UPDATE, PermissionScope.POD, "function", "Update functions"),
    PermissionDefinition(Permissions.FUNCTION_DELETE, PermissionScope.POD, "function", "Delete functions"),
    PermissionDefinition(Permissions.FUNCTION_EXECUTE, PermissionScope.POD, "function", "Execute functions"),
    PermissionDefinition(Permissions.WORKFLOW_READ, PermissionScope.POD, "workflow", "Read workflows"),
    PermissionDefinition(Permissions.WORKFLOW_CREATE, PermissionScope.POD, "workflow", "Create workflows"),
    PermissionDefinition(Permissions.WORKFLOW_UPDATE, PermissionScope.POD, "workflow", "Update workflows"),
    PermissionDefinition(Permissions.WORKFLOW_DELETE, PermissionScope.POD, "workflow", "Delete workflows"),
    PermissionDefinition(Permissions.WORKFLOW_EXECUTE, PermissionScope.POD, "workflow", "Execute workflows"),
    PermissionDefinition(Permissions.SCHEDULE_READ, PermissionScope.POD, "schedule", "Read schedules"),
    PermissionDefinition(Permissions.SCHEDULE_CREATE, PermissionScope.POD, "schedule", "Create schedules"),
    PermissionDefinition(Permissions.SCHEDULE_UPDATE, PermissionScope.POD, "schedule", "Update schedules"),
    PermissionDefinition(Permissions.SCHEDULE_DELETE, PermissionScope.POD, "schedule", "Delete schedules"),
    PermissionDefinition(Permissions.CONVERSATION_READ, PermissionScope.POD, "conversation", "Read conversations"),
    PermissionDefinition(Permissions.CONVERSATION_WRITE, PermissionScope.POD, "conversation", "Create and update conversations"),
    PermissionDefinition(Permissions.CONNECTOR_USE, PermissionScope.POD, "connector", "Use pod-enabled connectors"),
    PermissionDefinition(Permissions.CONNECTOR_MANAGE, PermissionScope.ORG, "connector", "Enable and configure organization connectors"),
    PermissionDefinition(Permissions.CONNECTOR_ACCOUNT_USE, PermissionScope.POD, "connector_account", "Use connected connector accounts"),
    PermissionDefinition(Permissions.CONNECTOR_ACCOUNT_MANAGE, PermissionScope.POD, "connector_account", "Manage connected connector accounts"),
    PermissionDefinition(Permissions.CONNECTOR_AUTH_CONFIG_MANAGE, PermissionScope.ORG, "connector_auth_config", "Manage organization connector auth configs"),
)

PERMISSION_BY_ID: dict[str, PermissionDefinition] = {
    definition.id: definition for definition in PERMISSION_DEFINITIONS
}

POD_VIEWER_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permissions.POD_READ,
        Permissions.DATASTORE_TABLE_READ,
        Permissions.DATASTORE_RECORD_READ,
        Permissions.FOLDER_READ,
        Permissions.APP_READ,
        Permissions.AGENT_READ,
        Permissions.FUNCTION_READ,
        Permissions.WORKFLOW_READ,
        Permissions.SCHEDULE_READ,
        Permissions.CONVERSATION_READ,
    }
)
POD_USER_PERMISSIONS: frozenset[str] = frozenset(
    set(POD_VIEWER_PERMISSIONS)
    | {
        Permissions.DATASTORE_RECORD_WRITE,
        Permissions.AGENT_EXECUTE,
        Permissions.FUNCTION_EXECUTE,
        Permissions.WORKFLOW_EXECUTE,
        # Pod users can create their own schedules. The schedule is owned by its
        # creator and defaults to PERSONAL visibility, and the owner-action set
        # lets the creator manage (read/update/delete) it without needing the
        # pod-wide SCHEDULE_UPDATE/SCHEDULE_DELETE permissions that editors hold.
        Permissions.SCHEDULE_CREATE,
        Permissions.CONVERSATION_WRITE,
        Permissions.CONNECTOR_USE,
        Permissions.CONNECTOR_ACCOUNT_USE,
    }
)
POD_EDITOR_PERMISSIONS: frozenset[str] = frozenset(
    set(POD_USER_PERMISSIONS)
    | {
        Permissions.DATASTORE_TABLE_CREATE,
        Permissions.DATASTORE_TABLE_UPDATE,
        Permissions.FOLDER_WRITE,
        Permissions.APP_CREATE,
        Permissions.APP_UPDATE,
        Permissions.APP_PUBLISH,
        Permissions.AGENT_CREATE,
        Permissions.AGENT_UPDATE,
        Permissions.FUNCTION_CREATE,
        Permissions.FUNCTION_UPDATE,
        Permissions.WORKFLOW_CREATE,
        Permissions.WORKFLOW_UPDATE,
        Permissions.SCHEDULE_CREATE,
        Permissions.SCHEDULE_UPDATE,
    }
)
POD_ADMIN_PERMISSIONS: frozenset[str] = frozenset(
    set(POD_EDITOR_PERMISSIONS)
    | {
        Permissions.POD_UPDATE,
        Permissions.POD_DELETE,
        Permissions.POD_MEMBER_MANAGE,
        Permissions.POD_ROLE_MANAGE,
        Permissions.DATASTORE_TABLE_DELETE,
        Permissions.FOLDER_DELETE,
        Permissions.APP_DELETE,
        Permissions.AGENT_DELETE,
        Permissions.FUNCTION_DELETE,
        Permissions.WORKFLOW_DELETE,
        Permissions.SCHEDULE_DELETE,
        Permissions.CONNECTOR_ACCOUNT_MANAGE,
    }
)

ORG_MEMBER_PERMISSIONS: frozenset[str] = frozenset({Permissions.ORG_READ})
ORG_EDITOR_PERMISSIONS: frozenset[str] = frozenset(
    set(ORG_MEMBER_PERMISSIONS)
    | {
        Permissions.ORG_UPDATE,
        Permissions.ORG_MEMBER_MANAGE,
        Permissions.ORG_INVITATION_MANAGE,
        Permissions.POD_CREATE,
        Permissions.CONNECTOR_MANAGE,
        Permissions.CONNECTOR_AUTH_CONFIG_MANAGE,
    }
)
ORG_OWNER_PERMISSIONS: frozenset[str] = frozenset(
    set(ORG_EDITOR_PERMISSIONS)
    | {
        Permissions.ORG_BILLING_MANAGE,
    }
)

SYSTEM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "ORG_MEMBER": ORG_MEMBER_PERMISSIONS,
    "ORG_EDITOR": ORG_EDITOR_PERMISSIONS,
    "ORG_OWNER": ORG_OWNER_PERMISSIONS,
    "POD_VIEWER": POD_VIEWER_PERMISSIONS,
    "POD_USER": POD_USER_PERMISSIONS,
    "POD_EDITOR": POD_EDITOR_PERMISSIONS,
    "POD_ADMIN": POD_ADMIN_PERMISSIONS,
}

IMPLIED_PERMISSIONS: dict[str, frozenset[str]] = {
    Permissions.APP_UPDATE: frozenset({Permissions.APP_READ}),
    Permissions.APP_DELETE: frozenset({Permissions.APP_UPDATE, Permissions.APP_READ}),
    Permissions.FOLDER_WRITE: frozenset({Permissions.FOLDER_READ}),
    Permissions.FOLDER_DELETE: frozenset({Permissions.FOLDER_WRITE, Permissions.FOLDER_READ}),
    Permissions.DATASTORE_TABLE_UPDATE: frozenset({Permissions.DATASTORE_TABLE_READ}),
    Permissions.DATASTORE_TABLE_DELETE: frozenset(
        {Permissions.DATASTORE_TABLE_UPDATE, Permissions.DATASTORE_TABLE_READ}
    ),
    Permissions.CONNECTOR_ACCOUNT_MANAGE: frozenset(
        {Permissions.CONNECTOR_ACCOUNT_USE}
    ),
    Permissions.CONVERSATION_WRITE: frozenset({Permissions.CONVERSATION_READ}),
    # Executing a workload requires reading its definition (an agent's
    # instructions, a function's source) to run it at all, so execute-only
    # grants must satisfy read checks. Read does NOT imply execute.
    Permissions.AGENT_EXECUTE: frozenset({Permissions.AGENT_READ}),
    Permissions.FUNCTION_EXECUTE: frozenset({Permissions.FUNCTION_READ}),
    Permissions.WORKFLOW_EXECUTE: frozenset({Permissions.WORKFLOW_READ}),
}


def equivalent_permission_ids(permission_id: str) -> set[str]:
    """Return permissions that can satisfy a check for ``permission_id``."""
    candidates = {permission_id}
    for candidate, implied in IMPLIED_PERMISSIONS.items():
        if permission_id in implied:
            candidates.add(candidate)
    return candidates
