"""Connector toolset: call an installed connector's operations directly.

Agents previously reached connectors only by shelling out to `lemma connectors
operations ...` inside the sandbox. That works, and still does, but it costs a
sandbox round trip per call and is unavailable to any agent without the
workspace toolset.

The loop is search, describe, run, through the pod's connectors: the agent names
a pod connector by its slug (`gmail`, `support-inbox`) and never an account.
Which account a call runs as is the connector's decision -- the person the
agent acts for, or the account its owner shared with the pod -- so nothing the
model types can make it act as someone else. Operations are found rather than
enumerated (an MCP server can expose hundreds), and their schemas are fetched
on demand rather than compiled into per-operation tools.

Arguments are validated here against the operation's own stored schema, and a
mismatch comes back as a structured result rather than an exception, so the
model can correct itself instead of the run failing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import jsonschema
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.domain.errors import DomainError
from app.core.redaction import redact_text
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.connectors.connector_access import (
    ConnectorServices,
    connector_execution_only,
    connector_services,
    find_file_result,
)
from app.modules.agent.tools.connectors.grant_offer import connector_grant_offer
from app.modules.agent.tools.connectors.models import (
    ConnectConnectorRequest,
    DescribeConnectorOperationRequest,
    RunConnectorOperationRequest,
    SearchConnectorOperationsRequest,
)
from app.modules.agent.tools.context import BaseAgentContext

_MAX_VIOLATIONS = 10

#: What a connector error's details may carry back to the model. Connector
#: errors already pass theirs through an allowlist and redaction; this is the
#: same short list again, so a domain error from elsewhere cannot widen it.
_DETAIL_KEYS = (
    "operation_name",
    "upstream_message",
    "upstream_status",
    "server_host",
    "reason",
    "retry_after",
    # The remedy for a connector that cannot run yet: which one, and where its
    # person connects or reconnects.
    "pod_connector",
    "connect_url",
)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A failure the model can act on, rather than one that ends the run.

    `success: False` is the uniform contract `result_is_failure` reads. Without
    it every connector failure counted as a successful tool call -- in spans,
    and in the `isError` an MCP client sees.
    """
    return {"success": False, "error": message, "code": code, **extra}


def _failure(
    exc: DomainError,
    *,
    connector: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    """A connector error, naming what failed and what the provider said.

    The error types fix their own message ("provider is temporarily
    unavailable"), so the text alone named neither the install, the operation,
    the server nor anything the server had said -- the parts a model needs to
    fix its call or tell the person which connector is down.
    """
    details = exc.details if isinstance(exc.details, dict) else {}
    carried = {
        key: redact_text(value) if isinstance(value, str) else value
        for key in _DETAIL_KEYS
        if (value := details.get(key)) is not None
    }
    named = {
        key: value
        for key, value in (("connector", connector), ("operation", operation))
        if value
    }
    return _error(
        exc.code or "connector_error", safe_error_text(exc), **named, **carried
    )


def _no_pod() -> dict[str, Any]:
    return _error(
        "no_pod",
        "This conversation is not bound to a pod, so connectors are unavailable.",
    )


connectors_toolset = FunctionToolset()


#: What the person's own account on a per-member connector's installation is
#: like, in the words a model repeats to them. Without it every listed
#: connector read as working, and agents told people Gmail was connected when
#: the first call was going to ask them to connect it.
_STANDING = {
    "CONNECTED": "connected",
    "REAUTH_REQUIRED": "needs reconnecting: a call returns a connect_url",
    None: "not connected yet: a call returns a connect_url",
}


def _standing(mode: str, auth_config_id: UUID, standing: dict[UUID, str]) -> str | None:
    if mode != "PER_MEMBER":
        return None
    found = standing.get(auth_config_id)
    return found if found in _STANDING else None


def _connector_line(connector: Any, standing: dict[UUID, str]) -> str:
    if connector.mode.value == "PER_MEMBER":
        mode = (
            "your person's own account, "
            + _STANDING[
                _standing(connector.mode.value, connector.auth_config_id, standing)
            ]
        )
    else:
        mode = "one shared account"
    purpose = f" -- {connector.purpose}" if connector.purpose else ""
    return f"- `{connector.slug}` ({connector.connector_id}, {mode}){purpose}"


#: Said of an installation the person has an account on and the pod has not
#: declared: naming it declares it, as a connector of each member's own account.
_OFFERED = "your person's own account; added to this pod on first use"


def _offer_line(offer: Any) -> str:
    return f"- `{offer.name}` ({offer.connector_id}, {_OFFERED})"


async def _usable(
    deps: BaseAgentContext,
) -> tuple[list[Any], list[Any], dict[UUID, str]]:
    """The pod's connectors this agent may use, the person's own
    installations the pod declares no connector of their own account for, and
    how the person's account stands on each installation."""
    async with connector_services(deps) as services:
        usable = await services.pod_connectors.usable_connectors(
            services.ctx, deps.pod_id
        )
        offered = await services.reach.offered(services.ctx, deps.pod_id)
        standing = await services.reach.account_standing(services.ctx)
    return usable, offered, standing


async def _search_one(
    services: Any,
    deps: BaseAgentContext,
    connector: str,
    *,
    query: str | None,
    limit: int,
) -> Any:
    """Operations of a pod connector, or of one of the person's own
    installations it names. Browsing an installation declares nothing;
    running one does (``slug_for``)."""
    offer = await services.reach.installation(services.ctx, deps.pod_id, connector)
    if offer is None:
        # Nothing connected yet: browse the installation or the catalog, so the
        # schema is known before anybody signs in.
        offer = await services.reach.unconnected(services.ctx, deps.pod_id, connector)
    if offer is not None:
        return await services.operations.discover_operations(
            offer.connector_id,
            query=query,
            limit=limit,
            kind=offer.kind,
            auth_config_id=offer.auth_config_id,
        )
    return await services.operations.pod_connector_operations(
        services.ctx, pod_id=deps.pod_id, slug=connector, query=query, limit=limit
    )


async def _describe_one(
    services: Any, deps: BaseAgentContext, connector: str, operation: str
) -> Any:
    offer = await services.reach.installation(services.ctx, deps.pod_id, connector)
    if offer is None:
        offer = await services.reach.unconnected(services.ctx, deps.pod_id, connector)
    if offer is not None:
        return await services.operations.get_operation_details(
            offer.connector_id,
            operation,
            kind=offer.kind,
            auth_config_id=offer.auth_config_id,
        )
    return await services.operations.pod_connector_operation(
        services.ctx, pod_id=deps.pod_id, slug=connector, operation_name=operation
    )


async def _describe_usable_connectors(
    ctx: RunContext[BaseAgentContext], tool_def: ToolDefinition
) -> ToolDefinition:
    """Name the connectors this agent may use in the tool's own description.

    Read per run from the agent's grants on the pod's connectors, so the model
    starts from what it can actually run instead of a `list_connectors` round
    trip -- and is never told about one it would be refused.
    """
    # No deps when the tool schemas are exported outside any run.
    if ctx.deps is None or ctx.deps.pod_id is None:
        return tool_def
    try:
        usable, offered, standing = await _usable(ctx.deps)
    except DomainError:
        return tool_def
    lines = [_connector_line(connector, standing) for connector in usable]
    lines += [_offer_line(offer) for offer in offered]
    listing = (
        "\n".join(lines)
        if lines
        else "- none: no connector in this pod is granted to you."
    )
    return replace(
        tool_def,
        description=f"{tool_def.description}\n\nConnectors you may use:\n{listing}",
    )


@connectors_toolset.tool
async def list_connectors(ctx: RunContext[BaseAgentContext]) -> dict[str, Any]:
    """List the pod's connectors you may run operations through.

    Each is named by its `connector` slug, which is how every other connector
    tool addresses it. `mode` says whose account a call uses: PER_MEMBER is
    the person you act for, SHARED is one account shared with the pod.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return _no_pod()
    try:
        usable, offered, standing = await _usable(deps)
    except DomainError as exc:
        # Raising here would end the agent's run over something it could
        # simply report.
        return _failure(exc)
    items = [
        {
            "connector": connector.slug,
            "connector_id": connector.connector_id,
            "mode": connector.mode.value,
            "purpose": connector.purpose,
            "status": connector.status.value,
            # PER_MEMBER: CONNECTED, REAUTH_REQUIRED, or None -- not connected.
            "your_account": _standing(
                connector.mode.value, connector.auth_config_id, standing
            ),
        }
        for connector in usable
    ]
    # The person's own apps the pod has not declared: they work the same way,
    # and the first run declares them.
    items += [
        {
            "connector": offer.name,
            "connector_id": offer.connector_id,
            "mode": "PER_MEMBER",
            "purpose": _OFFERED,
            "status": "READY",
        }
        for offer in offered
    ]
    return {"items": items, "count": len(items)}


@connectors_toolset.tool
async def search_connector_operations(
    ctx: RunContext[BaseAgentContext], request: SearchConnectorOperationsRequest
) -> dict[str, Any]:
    """Find operations by what they do, ranked by relevance.

    Start here rather than guessing an operation name: a connector can expose
    hundreds of operations, and the names are provider-specific. Leave
    `connector` unset to search every connector you may use -- each hit names
    the `connector` to run it through.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return _no_pod()

    async with connector_services(deps) as services:
        try:
            slugs = (
                [request.connector]
                if request.connector
                else [
                    connector.slug
                    for connector in await services.pod_connectors.usable_connectors(
                        services.ctx, deps.pod_id
                    )
                ]
                + [
                    offer.name
                    for offer in await services.reach.offered(services.ctx, deps.pod_id)
                ]
            )
            hits: list[dict[str, Any]] = []
            for slug in slugs:
                found = await _search_one(
                    services, deps, slug, query=request.query, limit=request.limit
                )
                hits.extend(
                    {**to_json_value(item), "connector": slug} for item in found.items
                )
        except DomainError as exc:
            return _failure(exc, connector=request.connector)
    hits.sort(key=lambda hit: hit.get("relevance_score") or 0, reverse=True)
    items = hits[: request.limit]
    return {"query": request.query, "items": items, "returned_count": len(items)}


@connectors_toolset.tool
async def describe_connector_operation(
    ctx: RunContext[BaseAgentContext], request: DescribeConnectorOperationRequest
) -> dict[str, Any]:
    """Get one operation's description and input/output schemas.

    Call this before running an operation you have not run before -- the input
    schema is what `run_connector_operation` validates `payload` against.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return _no_pod()

    async with connector_services(deps) as services:
        try:
            detail = await _describe_one(
                services, deps, request.connector, request.operation
            )
        except DomainError as exc:
            refused = exc
        else:
            refused = None
    if refused is not None:
        # A named agent not yet given this connector asks to be (grant_offer).
        offer = await connector_grant_offer(
            deps,
            refused,
            connector=request.connector,
            tool_name="describe_connector_operation",
            args=request.model_dump(mode="json"),
        )
        return offer or _failure(
            refused, connector=request.connector, operation=request.operation
        )
    # Whole input and output schemas; some connectors publish very large ones.
    return bounded_tool_payload(to_json_value(detail), what="operation schema")


def _validate_arguments(
    schema: dict[str, Any] | None, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Return a structured error if the arguments do not fit, else None."""
    if not schema:
        return None
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(arguments), key=lambda e: list(e.absolute_path)
    )
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


async def _connect_answer(
    services: ConnectorServices, deps: BaseAgentContext, name: str
) -> JsonObject:
    """What the agent hands its person for something not connected yet: a
    link to sign in, or -- when nobody installed it and they may not --
    who can.

    A link to the pod's Connections page on Lemma's own address rather than
    the provider's consent URL: it says what is being connected and to whom
    before sending the person on, works in a browser that blocks pop-ups, and
    installs Lemma's own app for the connector on the way when the
    organization has none. Directions through the menus were the old answer,
    and they were wrong as often as the menus moved.
    """
    unconnected = await services.reach.unconnected(services.ctx, deps.pod_id, name)
    if unconnected is None:
        return _error(
            "CONNECTOR_NOT_FOUND",
            f"Nothing called '{name}' is in this pod, this organization or the "
            "connector catalog. `list_connectors` names what you may use.",
            connector=name,
        )
    if unconnected.auth_config_id is None and not await services.reach.may_install(
        services.ctx
    ):
        return _error(
            "CONNECTOR_NOT_INSTALLED",
            f"{name} is not installed in this organization, and your person may "
            "not install it. An organization owner or editor adds it: "
            f"organization menu -> Connectors -> Available -> {name} -> Connect.",
            connector=name,
            connector_id=unconnected.connector_id,
        )
    return _error(
        "CONNECTION_REQUIRED",
        f"Your person has not connected {name}. Give them `connect_url` to "
        "sign in, then run this again -- it will run as their account.",
        connector=name,
        connector_id=unconnected.connector_id,
        connect_url=services.reach.connect_url(deps.pod_id, name),
    )


@connectors_toolset.tool
async def connect_connector(
    ctx: RunContext[BaseAgentContext], request: ConnectConnectorRequest
) -> JsonObject:
    """Get the link your person opens to connect an app (Gmail, Notion, ...).

    You do not need this before running an operation: `run_connector_operation`
    answers with the same `connect_url` when something is not connected. Use it
    when your person asks to connect something. Hand them `connect_url`
    as it is; do not describe menus instead.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return _no_pod()
    async with connector_services(deps) as services:
        try:
            declared = await services.pod_connectors.usable_connectors(
                services.ctx, deps.pod_id
            )
            if any(one.slug == request.connector for one in declared):
                return {
                    "success": True,
                    "connector": request.connector,
                    "connect_url": services.reach.connect_url(
                        deps.pod_id, request.connector
                    ),
                }
            if await services.reach.installation(
                services.ctx, deps.pod_id, request.connector
            ):
                return {
                    "success": True,
                    "connector": request.connector,
                    "already_connected": True,
                }
            return await _connect_answer(services, deps, request.connector)
        except DomainError as exc:
            return _failure(exc, connector=request.connector)


@connectors_toolset.tool(prepare=_describe_usable_connectors)
async def run_connector_operation(
    ctx: RunContext[BaseAgentContext], request: RunConnectorOperationRequest
) -> dict[str, Any]:
    """Run an operation through one of the pod's connectors.

    `payload` must match the operation's input schema -- fetch it with
    `describe_connector_operation` first. Whose account the call runs as is the
    connector's decision, not yours. To send a file (an email attachment, a
    Drive upload), pass it as `{"pod_path": "/me/report.pdf"}` in the file
    argument; a file on your machine has to be uploaded to the pod first. A
    file result larger than the inline limit is written to the pod datastore
    and returned as a reference; pass `output_path` to choose where it lands.
    A `CONNECTION_REQUIRED` or `REAUTH_REQUIRED` answer carries a `connect_url`
    to hand your person.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return _no_pod()

    installation = None
    try:
        # Phase 1, in a short scope: every DB read, the authorization check and
        # the credential resolution. The scope closes -- releasing the pooled
        # connection -- before anything reaches the provider.
        async with connector_services(deps) as services:
            # One of the person's own installations is declared as the pod's
            # connector of their own account on its first run, in this scope's
            # transaction: a refusal below rolls it back, so a refused call
            # leaves no connector in the pod. The grant card a named agent is
            # offered then names the installation, and approving declares it.
            installation = await services.reach.installation(
                services.ctx, deps.pod_id, request.connector
            )
            if installation is not None:
                slug = await services.reach.declare(
                    services.ctx, deps.pod_id, installation
                )
                request = request.model_copy(update={"connector": slug})
            elif await services.reach.unconnected(
                services.ctx, deps.pod_id, request.connector
            ):
                return await _connect_answer(services, deps, request.connector)
            detail = await services.operations.pod_connector_operation(
                services.ctx,
                pod_id=deps.pod_id,
                slug=request.connector,
                operation_name=request.operation,
            )
            payload = dict(request.payload or {})
            invalid = _validate_arguments(
                getattr(detail, "input_schema", None), payload
            )
            if invalid is not None:
                return invalid

            resolved = await services.operations.resolve_pod_connector_execution(
                services.ctx,
                pod_id=deps.pod_id,
                slug=request.connector,
                operation_name=request.operation,
                payload=payload,
                # An agent acts as the app where the provider allows it, so a
                # schedule keeps working after the person who set it up leaves.
                act_as="app",
            )
            # The files the arguments name, read as this agent -- its delegated
            # grants, not the account owner's -- so a connector call cannot send
            # anything the agent could not have read itself.
            resolved = await services.files.prepare(resolved)

        # Phase 2: the provider call, holding no connection.
        async with connector_execution_only() as operations:
            response = await operations.execute_resolved(resolved)
        # Phase 3: a file result lands in the pod when it is large or asked for.
        found = await find_file_result(response)
        if found is not None:
            async with connector_services(deps) as services:
                response = await services.files.capture(
                    response,
                    found,
                    connector_id=resolved.connector_id,
                    output_path=request.output_path,
                )
    except DomainError as exc:
        # A named agent not yet given this connector asks to be (grant_offer).
        offer = await connector_grant_offer(
            deps,
            exc,
            connector=request.connector,
            tool_name="run_connector_operation",
            args=request.model_dump(mode="json"),
            auth_config_id=installation.auth_config_id if installation else None,
        )
        # Connector failures are information for the model (wrong argument,
        # account needs connecting), not a reason to end the run.
        return offer or _failure(
            exc, connector=request.connector, operation=request.operation
        )
    return bounded_tool_payload(to_json_value(response), what="connector response")
