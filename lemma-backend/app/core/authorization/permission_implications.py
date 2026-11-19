"""What holding one permission implies, and the questions read off it.

Split out of ``permissions.py``, the registry, which is at the size the
architecture ratchet allows; ``permissions`` re-exports all three names.
"""

from __future__ import annotations

from app.core.authorization.permissions import Permissions

IMPLIED_PERMISSIONS: dict[str, frozenset[str]] = {
    Permissions.APP_UPDATE: frozenset({Permissions.APP_READ}),
    Permissions.APP_DELETE: frozenset({Permissions.APP_UPDATE, Permissions.APP_READ}),
    Permissions.FOLDER_WRITE: frozenset({Permissions.FOLDER_READ}),
    Permissions.FOLDER_DELETE: frozenset(
        {Permissions.FOLDER_WRITE, Permissions.FOLDER_READ}
    ),
    Permissions.DATASTORE_TABLE_UPDATE: frozenset({Permissions.DATASTORE_TABLE_READ}),
    Permissions.DATASTORE_TABLE_DELETE: frozenset(
        {Permissions.DATASTORE_TABLE_UPDATE, Permissions.DATASTORE_TABLE_READ}
    ),
    Permissions.CONVERSATION_WRITE: frozenset({Permissions.CONVERSATION_READ}),
    # Whoever may write to anybody may write to themselves: this is how every
    # person who writes in channels holds `channel.notify_self` without a role
    # row changing, and so passes the invoker ceiling when their software uses
    # it. Not transitive (`equivalent_permission_ids` reads one level), so
    # `channel.manage` names it as well.
    Permissions.CHANNEL_WRITE: frozenset(
        {Permissions.CHANNEL_READ, Permissions.CHANNEL_NOTIFY_SELF}
    ),
    Permissions.CHANNEL_MANAGE: frozenset(
        {
            Permissions.CHANNEL_WRITE,
            Permissions.CHANNEL_READ,
            Permissions.CHANNEL_NOTIFY_SELF,
        }
    ),
    # Writing to a contact is done looking at who they are and what they said.
    Permissions.CONTACT_MESSAGE: frozenset({Permissions.CONTACT_READ}),
    Permissions.MACHINE_MANAGE: frozenset({Permissions.MACHINE_USE}),
    # Executing a workload requires reading its definition (an agent's
    # instructions, a function's source) to run it at all, so execute-only
    # grants must satisfy read checks. Read does NOT imply execute.
    Permissions.AGENT_EXECUTE: frozenset({Permissions.AGENT_READ}),
    Permissions.FUNCTION_EXECUTE: frozenset({Permissions.FUNCTION_READ}),
    Permissions.WORKFLOW_EXECUTE: frozenset({Permissions.WORKFLOW_READ}),
    # Deleting a workload requires reading it first (the delete flow loads the
    # resource by name), so a single delete grant is sufficient — matching the
    # app/folder/table families where delete implies read.
    Permissions.AGENT_DELETE: frozenset({Permissions.AGENT_READ}),
    Permissions.FUNCTION_DELETE: frozenset({Permissions.FUNCTION_READ}),
    Permissions.WORKFLOW_DELETE: frozenset({Permissions.WORKFLOW_READ}),
}


def equivalent_permission_ids(permission_id: str) -> set[str]:
    """Return permissions that can satisfy a check for ``permission_id``."""
    candidates = {permission_id}
    for candidate, implied in IMPLIED_PERMISSIONS.items():
        if permission_id in implied:
            candidates.add(candidate)
    return candidates


def pod_principal_may_hold(permission_id: str) -> bool:
    """Whether another pod may ever hold ``permission_id`` here.

    Reading, and running this pod's assistant -- the link itself. Nothing that
    changes anything, whatever a grant row says. Nor the pod's contacts: people
    who wrote to this pod did not write to the pods it is linked to -- and
    its channels carry what they said.
    """
    if permission_id in (Permissions.CONTACT_READ, Permissions.CHANNEL_READ):
        return False
    return permission_id.endswith(".read") or permission_id == Permissions.AGENT_EXECUTE
