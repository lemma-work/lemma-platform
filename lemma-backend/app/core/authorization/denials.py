"""What a refusal says: the resource it was about, and the one command that fixes it.

Split from ``context`` so the decision types read on their own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.authorization.context import AuthorizationDecision


# Grantee kind -> the CLI command that adds a grant to it. A denial is the moment
# the operator needs the fix, and the fix is one command.
_GRANT_COMMAND_BY_TYPE = {
    "datastore_table": "<table>:read,write",
    "folder": "<path>:read",
    "document": "doc:<path>:read",
    "function": "function:<name>:execute",
    "agent": "agent:<name>:execute",
    "workflow": "workflow:<name>:execute",
    "connector": "connector:<name>:use",
    "connector_account": "account:<id>:use",
    "app": "app:<name>:read",
    "schedule": "schedule:<name>:read",
}


def denial_resource_details(decision: "AuthorizationDecision") -> dict[str, object]:
    resource = decision.resource
    if resource is None:
        return {}
    details: dict[str, object] = {"resource_type": resource.resource_type.value}
    if decision.resource_name:
        details["resource_name"] = decision.resource_name
    return details


def denial_message(permission_id: str, decision: "AuthorizationDecision") -> str:
    """Say what was denied AND on what.

    "Missing permission datastore.table.read" is unactionable when the caller
    touches several tables: it names the verb, never the noun. The docs have
    always told operators the error names the resource, so make that true.
    """
    resource = decision.resource
    if resource is None:
        return f"Missing permission {permission_id}"
    resource_type = resource.resource_type.value
    target = f" on {resource_type}"
    if decision.resource_name:
        target += f" '{decision.resource_name}'"
    message = f"Missing permission {permission_id}{target}"
    if decision.reason_code == "DELEGATION_EXCEEDS_INVOKER":
        # The workload is granted this; the person it is acting for is not. Say
        # so, because the usual fix — grant the workload more — is the one thing
        # that cannot work here.
        return (
            f"{message} — the person this run acts for does not have it, and a "
            f"workload never exceeds its invoker. Give that person the "
            f"permission, or run this as someone who already has it."
        )
    if decision.reason_code == "MISSING_WORKLOAD_RESOURCE_GRANT":
        spec = _GRANT_COMMAND_BY_TYPE.get(resource_type)
        if spec and decision.resource_name:
            spec = spec.replace("<table>", decision.resource_name)
            spec = spec.replace("<path>", decision.resource_name)
            spec = spec.replace("<name>", decision.resource_name)
            spec = spec.replace("<id>", decision.resource_name)
            message += (
                f" — grant it with `lemma agents|functions permissions add "
                f"<workload> {spec}`"
            )
    return message
