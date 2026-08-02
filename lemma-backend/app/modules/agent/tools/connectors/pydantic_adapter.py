"""Connector toolset: call an installed connector's operations directly.

Agents previously reached connectors only by shelling out to `lemma connectors
operations ...` inside the sandbox. That works, and still does, but it costs a
sandbox round trip per call and is unavailable to any agent without the
workspace toolset.

The loop is search, describe, run: an org can have thousands of operations once
a couple of MCP servers are installed, so operations are found rather than
enumerated, and their schemas are fetched on demand rather than compiled into
per-operation tools.

Arguments are validated here against the operation's own stored schema, and a
mismatch comes back as a structured result rather than an exception, so the
model can correct itself instead of the run failing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import jsonschema
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.tools.connectors.connector_access import connector_services
from app.modules.agent.tools.connectors.models import (
    DescribeConnectorOperationRequest,
    RunConnectorOperationRequest,
    SearchConnectorOperationsRequest,
)
from app.modules.agent.tools.context import BaseAgentContext

_MAX_VIOLATIONS = 10


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A failure the model can act on, rather than one that ends the run."""
    return {"error": code, "message": message, **extra}


def _no_organization() -> dict[str, Any]:
    return _error(
        "no_organization",
        "This conversation is not bound to an organization, so connectors are "
        "unavailable.",
    )


connectors_toolset = FunctionToolset()


@connectors_toolset.tool
async def list_connectors(ctx: RunContext[BaseAgentContext]) -> dict[str, Any]:
    """List the connectors installed in this organization that you may use.

    Returns each install's `auth_config` name, which is how every other
    connector tool addresses it.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        configs, _ = await services.connector.list_auth_configs(
            user_id=deps.user_id, organization_id=deps.org_id, limit=200
        )
        items = [
            {
                "auth_config": config.name,
                "connector_id": config.connector_id,
                "kind": config.kind.value,
                "status": config.status.value
                if hasattr(config.status, "value")
                else str(config.status),
                "is_default": config.is_default,
            }
            for config in configs
        ]
    return {"items": items, "count": len(items)}


@connectors_toolset.tool
async def search_connector_operations(
    ctx: RunContext[BaseAgentContext], request: SearchConnectorOperationsRequest
) -> dict[str, Any]:
    """Find operations on an installed connector, ranked by relevance.

    Start here rather than guessing an operation name: an install can expose
    hundreds of operations, and the names are provider-specific.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        try:
            found = await services.operations.discover_operations_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                query=request.query,
                limit=request.limit,
            )
        except DomainError as exc:
            return _error(exc.code or "connector_error", str(exc))
    return to_json_value(found)


@connectors_toolset.tool
async def describe_connector_operation(
    ctx: RunContext[BaseAgentContext], request: DescribeConnectorOperationRequest
) -> dict[str, Any]:
    """Get one operation's description and input/output schemas.

    Call this before running an operation you have not run before -- the input
    schema is what `run_connector_operation` validates `arguments` against.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        try:
            detail = await services.operations.get_operation_details_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
            )
        except DomainError as exc:
            return _error(exc.code or "connector_error", str(exc))
    return to_json_value(detail)


def _validate_arguments(
    schema: dict[str, Any] | None, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Return a structured error if the arguments do not fit, else None."""
    if not schema:
        return None
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    return _error(
        "invalid_arguments",
        "The arguments do not match this operation's input schema.",
        violations=[
            {
                "path": "/".join(str(part) for part in error.absolute_path) or "(root)",
                "message": error.message,
            }
            for error in errors[:_MAX_VIOLATIONS]
        ],
        # Handed back so the model can correct itself without another round trip.
        input_schema=schema,
    )


@connectors_toolset.tool
async def run_connector_operation(
    ctx: RunContext[BaseAgentContext], request: RunConnectorOperationRequest
) -> dict[str, Any]:
    """Run an operation on an installed connector.

    `arguments` must match the operation's input schema -- fetch it with
    `describe_connector_operation` first. A file result larger than the inline
    limit is written to the pod datastore and returned as a reference; pass
    `output_path` to choose where it lands.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    account_id: UUID | None = None
    if request.account_id:
        try:
            account_id = UUID(request.account_id)
        except ValueError:
            return _error("invalid_account_id", "account_id must be a UUID.")

    async with connector_services(deps) as services:
        try:
            detail = await services.operations.get_operation_details_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
            )
            invalid = _validate_arguments(
                getattr(detail, "input_schema", None), dict(request.arguments or {})
            )
            if invalid is not None:
                return invalid

            payload = dict(request.arguments or {})
            if request.output_path:
                payload["output_path"] = request.output_path

            resolved = await services.operations.resolve_execution_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
                payload=payload,
                actor=services.ctx,
                account_id=account_id,
            )
            response = await services.operations.execute_resolved(resolved)
        except DomainError as exc:
            # Connector failures are information for the model (wrong argument,
            # account needs reconnecting), not a reason to end the run.
            return _error(exc.code or "connector_error", str(exc))
    return to_json_value(response)
