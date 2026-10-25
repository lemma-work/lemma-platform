"""What an approval card says "allow for this conversation" allows, in words.

A session approval is permission-wide: approving one record's delete for the
conversation lets the agent delete records in any table, without asking, until
the conversation's approvals lapse. That is the decided design, and it is only
fair if the card says so. "Approve for session" said none of it -- a person
read it as "this delete, and the next one like it", and agreed to more.

So the option is named by what it covers. The TypeScript card keeps the same
table (``lemma-frontend/src/thread/approval-scope.ts``); a test holds the two
to the same words.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.authorization.permissions import PERMISSION_BY_ID, Permissions

#: The permissions an agent is refused and asks for, each as what it lets the
#: agent do anywhere in the pod. Anything else falls back to its description.
_SCOPE: dict[str, str] = {
    Permissions.DATASTORE_TABLE_READ: "read any table",
    Permissions.DATASTORE_TABLE_CREATE: "create tables",
    Permissions.DATASTORE_TABLE_UPDATE: "change any table's columns and settings",
    Permissions.DATASTORE_TABLE_DELETE: "delete any table",
    Permissions.DATASTORE_RECORD_READ: "read records in any table",
    Permissions.DATASTORE_RECORD_WRITE: "write records in any table",
    Permissions.DATASTORE_RECORD_DELETE: "delete records in any table",
    Permissions.FOLDER_READ: "read any folder or file",
    Permissions.FOLDER_WRITE: "write in any folder",
    Permissions.FOLDER_DELETE: "delete any folder or file",
    Permissions.FOLDER_SHARE: "make public links to any file",
    Permissions.APP_UPDATE: "change any app",
    Permissions.APP_DELETE: "delete any app",
    Permissions.APP_PUBLISH: "publish any app",
    Permissions.AGENT_EXECUTE: "run any agent",
    Permissions.AGENT_UPDATE: "change any agent",
    Permissions.AGENT_DELETE: "delete any agent",
    Permissions.FUNCTION_EXECUTE: "run any function",
    Permissions.FUNCTION_UPDATE: "change any function",
    Permissions.FUNCTION_DELETE: "delete any function",
    Permissions.WORKFLOW_EXECUTE: "run any workflow",
    Permissions.WORKFLOW_UPDATE: "change any workflow",
    Permissions.WORKFLOW_DELETE: "delete any workflow",
    Permissions.SCHEDULE_UPDATE: "change any schedule",
    Permissions.SCHEDULE_DELETE: "delete any schedule",
    Permissions.CONNECTOR_USE: "use any of the pod's connectors",
    Permissions.CONNECTOR_ACCOUNT_USE: "use any account shared with the pod",
    Permissions.POD_DELETE: "delete the pod",
}

#: How long it lasts, said the same way everywhere.
FOR_THE_CONVERSATION = "for the rest of this conversation"


def permission_scope(permission_id: str) -> str:
    """One permission as what it lets the agent do, lower case."""
    if permission_id in _SCOPE:
        return _SCOPE[permission_id]
    definition = PERMISSION_BY_ID.get(permission_id)
    if definition is None:
        return permission_id
    text = definition.description
    return text[:1].lower() + text[1:]


def session_scope_sentence(permission_ids: Iterable[str] | None) -> str:
    """What "allow for this conversation" allows: "Delete records in any table
    for the rest of this conversation".

    With no permission named, the only thing a session approval covers is the
    exact call itself, repeated -- so it says that instead.
    """
    phrases = list(
        dict.fromkeys(permission_scope(p) for p in sorted(permission_ids or ()) if p)
    )
    if not phrases:
        return f"Run this exact command again without asking, {FOR_THE_CONVERSATION}"
    joined = (
        phrases[0]
        if len(phrases) == 1
        else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    )
    return f"{joined[:1].upper()}{joined[1:]} {FOR_THE_CONVERSATION}"


def grant_note(connector: str) -> str:
    """What approving a card that offers a connector does, said plainly.

    Unlike every other approval it is not a loan: the agent keeps the grant.
    """
    what = f"the {connector} connector" if connector else "this connector"
    return (
        f"Approving gives this agent {what} from now on, in every "
        "conversation, until someone removes it."
    )
