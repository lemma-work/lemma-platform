"""Request models for the connector toolset."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import JsonObject

_CONNECTOR = (
    "The pod connector, by its slug, as `list_connectors` names it (e.g. "
    "`gmail`, `support-inbox`)."
)


class SearchConnectorOperationsRequest(BaseModel):
    # Optional on purpose. Requiring it made every task start with
    # list_connectors and a guess about which connector does email -- and the
    # guess is wrong whenever a pod has both Gmail and Outlook.
    connector: str | None = Field(
        default=None,
        description=(
            _CONNECTOR + " Omit to search every connector you may use in this "
            "pod -- prefer that when you know the task but not which connector "
            "provides it."
        ),
    )
    query: str | None = Field(
        default=None, description="Free text to rank operations by; omit to list."
    )
    limit: int = Field(default=20, ge=1, le=100)


class DescribeConnectorOperationRequest(BaseModel):
    connector: str = Field(description=_CONNECTOR)
    operation: str = Field(description="Operation name from search results.")


class RunConnectorOperationRequest(BaseModel):
    """No account argument: the connector decides whose account a call runs as.

    A per-member connector uses the account of the person you act for; a
    shared one uses the account its owner shared with the pod. An
    ``account_id`` here was a free argument a model could fill in to act as a
    colleague.
    """

    connector: str = Field(description=_CONNECTOR)
    operation: str = Field(description="Operation name from search results.")
    # Free-form rather than a per-operation model: compiling one model per
    # operation would blow the tool budget, need invalidating on every refresh,
    # and change whenever any tenant adds an MCP server. The schema is fetched on
    # demand by describe_connector_operation, and arguments are validated against
    # it server-side.
    payload: JsonObject = Field(
        default_factory=dict,
        description=(
            "Operation arguments, matching its input_schema. A file argument "
            'takes a pod file: {"pod_path": "/me/report.pdf"}, or '
            '{"file_id": "..."}.'
        ),
    )
    output_path: str | None = Field(
        default=None,
        description="Pod path to save a file result to, e.g. /me/report.pdf.",
    )
