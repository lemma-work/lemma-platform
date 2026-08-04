"""Request models for the connector toolset."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import JsonObject


class SearchConnectorOperationsRequest(BaseModel):
    # Optional on purpose. Requiring it made every task start with
    # list_connectors and a guess about which install does email -- and the
    # guess is wrong whenever an org has both Gmail and Outlook installed.
    # Omitting it searches every install and each hit carries the auth_config
    # to run it against, so "send an email" is search then run.
    auth_config: str | None = Field(
        default=None,
        description=(
            "Installed connector to search, by its auth-config name. Omit to "
            "search every connector installed in the organization -- prefer "
            "that when you know the task but not which install provides it."
        ),
    )
    query: str | None = Field(
        default=None, description="Free text to rank operations by; omit to list."
    )
    limit: int = Field(default=20, ge=1, le=100)


class DescribeConnectorOperationRequest(BaseModel):
    auth_config: str = Field(description="Installed connector, by auth-config name.")
    operation: str = Field(description="Operation name from search results.")


class RunConnectorOperationRequest(BaseModel):
    auth_config: str = Field(description="Installed connector, by auth-config name.")
    operation: str = Field(description="Operation name from search results.")
    # Free-form rather than a per-operation model: compiling one model per
    # operation would blow the tool budget, need invalidating on every refresh,
    # and change whenever any tenant adds an MCP server. The schema is fetched on
    # demand by describe_connector_operation, and arguments are validated against
    # it server-side.
    arguments: JsonObject = Field(
        default_factory=dict,
        description="Operation arguments, matching its input_schema.",
    )
    account_id: str | None = Field(
        default=None,
        description="Connected account to use; omit for the caller's own.",
    )
    output_path: str | None = Field(
        default=None,
        description="Pod path to save a file result to, e.g. /me/report.pdf.",
    )
