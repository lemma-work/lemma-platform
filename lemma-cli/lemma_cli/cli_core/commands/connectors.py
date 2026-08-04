from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer
from lemma_sdk.openapi_client.models.account_create_schema import AccountCreateSchema
from lemma_sdk.openapi_client.models.auth_config_create_schema import AuthConfigCreateSchema

from ..confirm import confirm_destructive
from ..io import emit, list_items, to_plain
from ..payload import read_json
from ..state import console, run_with_client, state_from_ctx
from ..context import org_for

app = typer.Typer(help="Connector, account, and operation commands.")

auth_configs_app = typer.Typer(help="Organization connector auth config commands.")
accounts_app = typer.Typer(help="Connected connector account commands.")
connect_requests_app = typer.Typer(help="Connector connect request commands.")
operations_app = typer.Typer(
    help="Connector operation search, details, and execution commands."
)
triggers_app = typer.Typer(help="Connector trigger list and detail commands.")


# The installed auth configs, memoized per client for the life of one command.
# Resolution consults them up to twice (once to classify a positional, once to
# resolve it), and that should still cost one request, not two.
_AUTH_CONFIG_CACHE: dict[int, list[dict]] = {}


def _auth_config_items(client: Any) -> list[dict]:
    """Installed auth configs, or an empty list when they can't be listed.

    A client that predates the auth-configs resource (or a listing that errors)
    is not fatal: resolution falls back to using the caller's selector verbatim,
    which is exactly what the CLI did before it could resolve connector ids.
    """
    key = id(client)
    if key in _AUTH_CONFIG_CACHE:
        return _AUTH_CONFIG_CACHE[key]
    try:
        data = to_plain(client.connectors.auth_configs.list(limit=200))
    except Exception:  # noqa: BLE001 — resolution degrades, never blocks
        data = None
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        items = [item for item in data.get("items", []) if isinstance(item, dict)]
    else:
        items = []
    _AUTH_CONFIG_CACHE[key] = items
    return items


def _describe_auth_configs(items: list[dict]) -> str:
    return ", ".join(
        f"{item.get('name') or item.get('id')} (connector {item.get('connector_id') or '?'}"
        f", kind {item.get('kind') or '?'})"
        for item in items
    )


def _resolve_auth_config(client: Any, selector: str | None) -> str:
    """Resolve an auth-config SELECTOR to the exact auth-config name every
    operations/triggers endpoint is keyed by.

    Accepts, in order of preference:

    * an auth-config name — used as-is;
    * a **connector id** (`gmail`, `slack`) — resolved to that connector's
      install, preferring the one flagged default. This is the important one:
      the connector id is what an agent already knows from the task ("read my
      Gmail"), while the auth-config name is an org-local string it could only
      learn by running `overview` first;
    * nothing — the sole install, if there is exactly one.

    A selector that matches nothing raises with the installs that *do* exist, so
    the next command is obvious instead of requiring a separate discovery call.
    """
    items = _auth_config_items(client)
    if not items:
        # Nothing to resolve against. An explicit selector is still the caller's
        # best guess — pass it through rather than refusing on our own ignorance.
        if selector is not None:
            return selector
        raise typer.BadParameter(
            "No connectors are installed in this organization. Install one with "
            "`lemma connectors auth-configs create <connector>`, then connect an "
            "account.",
            param_hint="AUTH_CONFIG",
        )

    if selector is None:
        if len(items) == 1:
            name = str(items[0].get("name") or items[0].get("id") or "")
            typer.echo(f"Using auth config: {name}", err=True)
            return name
        raise typer.BadParameter(
            "Several connectors are installed — name one (or pass its connector "
            f"id): {_describe_auth_configs(items)}",
            param_hint="AUTH_CONFIG",
        )

    needle = selector.strip().casefold()
    for item in items:
        if str(item.get("name") or "").casefold() == needle:
            return str(item["name"])
    for item in items:
        if str(item.get("id") or "").casefold() == needle:
            return str(item.get("name") or item["id"])

    # Fall back to the connector id: pick the default install, else the only one.
    by_connector = [
        item
        for item in items
        if str(item.get("connector_id") or item.get("app_id") or "").casefold() == needle
    ]
    if by_connector:
        preferred = next(
            (item for item in by_connector if item.get("is_default")), by_connector[0]
        )
        name = str(preferred.get("name") or preferred.get("id") or "")
        if len(by_connector) > 1:
            typer.echo(
                f"Connector '{selector}' has {len(by_connector)} installs; using "
                f"'{name}'. Pass an auth-config name to choose another.",
                err=True,
            )
        return name

    raise typer.BadParameter(
        f"No connector install matches '{selector}'. Installed: "
        f"{_describe_auth_configs(items)}. Pass an auth-config name or a connector id.",
        param_hint="AUTH_CONFIG",
    )


def _looks_like_auth_config(client: Any, value: str) -> bool:
    """True when `value` names an installed auth config or connector — used to
    tell a leading auth-config positional apart from a query/operation that was
    passed where the positional used to sit."""
    items = _auth_config_items(client)
    if not items:
        # Can't tell — keep the historical reading (leading positional is the
        # auth config) so nothing that worked before starts being misread.
        return True
    needle = value.strip().casefold()
    for item in items:
        if needle in {
            str(item.get("name") or "").casefold(),
            str(item.get("id") or "").casefold(),
            str(item.get("connector_id") or item.get("app_id") or "").casefold(),
        }:
            return True
    return False


def _split_target_and_rest(
    client: Any, first: str | None, second: str | None, *, option: str | None
) -> tuple[str | None, str | None]:
    """Disambiguate the legacy `<auth-config> <text>` positional pair.

    `lemma connectors operations search "send email"` used to bind the query to
    the auth-config positional and 404 — the auto-discovery the help advertised
    could never fire, because the slot was full. Resolve it by meaning, not
    position: a first positional that names a real install is the target, and
    anything else is the query/operation.
    """
    if option is not None:
        # An explicit --auth-config wins; both positionals are then content.
        return option, first if second is None else second
    if first is None:
        return None, second
    if second is not None:
        return first, second
    return (first, None) if _looks_like_auth_config(client, first) else (None, first)


def _strip_body_fields(obj: Any) -> Any:
    """Recursively remove keys whose values are large HTML blobs (>500 chars containing HTML tags)."""
    if isinstance(obj, dict):
        return {
            k: _strip_body_fields(v)
            for k, v in obj.items()
            if not (
                isinstance(v, str)
                and len(v) > 500
                and any(tag in v.lower() for tag in ("<html", "<div", "<table", "<body"))
            )
        }
    if isinstance(obj, list):
        return [_strip_body_fields(item) for item in obj]
    return obj


def _connectors(client):  # type: ignore[no-untyped-def]
    return client.connectors


def _list_connectors(client, *, limit: int):  # type: ignore[no-untyped-def]
    api = _connectors(client)
    if hasattr(api, "apps"):
        return api.apps.list(limit=limit)
    return api.list_connectors(limit=limit)


def _list_accounts(client, state, *, connector: str | None, limit: int):  # type: ignore[no-untyped-def]
    api = _connectors(client)
    if hasattr(api, "accounts"):
        return api.accounts.list(app=connector, limit=limit)
    return api.list_accounts(
        organization_id=org_for(client, state),
        connector_id=connector,
        limit=limit,
    )


def _list_triggers(  # type: ignore[no-untyped-def]
    client,
    state,
    *,
    auth_config: str | None,
    search: str | None,
    limit: int,
):
    api = _connectors(client)
    resolved = _resolve_auth_config(client, auth_config)
    if hasattr(api, "triggers"):
        return api.triggers.list(resolved, search=search, limit=limit)
    return api.list_connector_triggers(
        organization_id=org_for(client, state),
        auth_config_name=resolved,
        search=search,
        limit=limit,
    )


def _get_trigger(client, state, *, auth_config: str | None, trigger: str):  # type: ignore[no-untyped-def]
    api = _connectors(client)
    resolved = _resolve_auth_config(client, auth_config)
    if hasattr(api, "triggers"):
        return api.triggers.get(resolved, trigger)
    return api.get_connector_trigger(
        organization_id=org_for(client, state),
        auth_config_name=resolved,
        trigger_name=trigger,
    )


@app.command("list")
def list_connectors(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List available connectors."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: _list_connectors(client, limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_connector(
    ctx: typer.Context,
    connector: str = typer.Argument(...),
) -> None:
    """Show a connector."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: (
            client.connectors.apps.get(connector)
            if hasattr(client.connectors, "apps")
            else client.connectors.get_connector(connector)
        ),
    )
    if result is not None:
        emit(state, result)


@accounts_app.command("list")
def list_accounts(
    ctx: typer.Context,
    connector: str | None = typer.Option(
        None,
        "--connector",
        "--app",
        help="Filter by connector id/name.",
    ),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List connected accounts."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _list_accounts(
            client,
            s,
            connector=connector,
            limit=limit,
        ),
    )
    if result is not None:
        emit(state, result)


@accounts_app.command("get")
def get_account(
    ctx: typer.Context,
    account: str = typer.Argument(...),
) -> None:
    """Show a connected account."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.accounts.get(account),
    )
    if result is not None:
        emit(state, result)


@accounts_app.command("delete")
def delete_account(
    ctx: typer.Context,
    account: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a connected account."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete account {account}?", yes)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.accounts.delete(account),
    )
    emit(state, result if result is not None else {"ok": True})


@accounts_app.command("create")
def create_account(
    ctx: typer.Context,
    credentials_json: str | None = typer.Option(
        None, "--data", "-d", "--credentials-json", help="Credentials JSON payload."
    ),
    credentials_file: Path | None = typer.Option(
        None,
        "--credentials-file",
        "--file",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    auth_config: str | None = typer.Option(
        None,
        "--auth-config",
        "--auth-config-name",
        help="Auth config name to connect.",
    ),
    auth_config_id: str | None = typer.Option(None, "--auth-config-id"),
    provider_account_id: str | None = typer.Option(None, "--provider-account-id"),
    email: str | None = typer.Option(None, "--email"),
    preferences_json: str | None = typer.Option(None, "--preferences-json"),
    preferences_file: Path | None = typer.Option(
        None,
        "--preferences-file",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    allowed_scope: list[str] | None = typer.Option(
        None,
        "--allowed-scope",
        help="Allowed scope. Repeat for multiple scopes.",
    ),
) -> None:
    """Connect an account with credentials."""
    if bool(auth_config) == bool(auth_config_id):
        raise typer.BadParameter(
            "Use exactly one: --auth-config or --auth-config-id",
        )
    credentials = read_json(credentials_json, credentials_file, required=True)
    preferences = read_json(preferences_json, preferences_file, required=False) or None
    request_data = {
        "credentials": credentials,
        "provider_account_id": provider_account_id,
        "email": email,
        "preferences": preferences,
        "allowed_scopes": allowed_scope or None,
    }
    if auth_config:
        request_data["auth_config_name"] = auth_config
    if auth_config_id:
        request_data["auth_config_id"] = auth_config_id
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.accounts.create(
                auth_config or auth_config_id or "",
                AccountCreateSchema.from_dict(request_data),
            )
            if hasattr(client.connectors, "accounts")
            else client.connectors.create_account(
                organization_id=org_for(client, s),
                auth_config_name=auth_config,
                auth_config_id=auth_config_id,
                credentials=credentials,
                provider_account_id=provider_account_id,
                email=email,
                preferences=preferences,
                allowed_scopes=allowed_scope or None,
            )
        ),
    )
    if result is not None:
        emit(state, result)


@auth_configs_app.command("list")
def list_auth_configs(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List auth configs."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.auth_configs.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@auth_configs_app.command("get")
def get_auth_config(
    ctx: typer.Context,
    auth_config: str = typer.Argument(...),
) -> None:
    """Show an auth config."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.auth_configs.get(auth_config),
    )
    if result is not None:
        emit(state, result)


@auth_configs_app.command("create")
def create_auth_config(
    ctx: typer.Context,
    connector: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name"),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Which of the connector's kinds to install. Optional when it offers one.",
    ),
    config_source: str = typer.Option("SYSTEM_DEFAULT", "--config-source"),
    credential_json: str | None = typer.Option(
        None, "--data", "-d", "--config-json", help="Install config JSON payload."
    ),
    credential_file: Path | None = typer.Option(
        None,
        "--connector-config-file",
        "--file",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Create an auth config for a connector."""
    credential_config = (
        read_json(credential_json, credential_file, required=False) or None
    )
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.auth_configs.create(
                AuthConfigCreateSchema.from_dict(
                    {
                        "connector_id": connector,
                        "name": name,
                        "kind": kind,
                        "config_source": config_source,
                        "config": credential_config,
                    }
                )
            )
            if hasattr(client.connectors, "auth_configs")
            else client.connectors.create_auth_config(
                organization_id=org_for(client, s),
                connector_id=connector,
                name=name,
                kind=kind,
                config_source=config_source,
                config=credential_config,
            )
        ),
    )
    if result is not None:
        emit(state, result)


@auth_configs_app.command("update")
def update_auth_config(
    ctx: typer.Context,
    auth_config: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", help="Rename this install."),
    status: str | None = typer.Option(None, "--status", help="ACTIVE or DISABLED."),
    is_default: bool | None = typer.Option(
        None,
        "--default/--no-default",
        help="Make this the install a bare connector id resolves to.",
    ),
    config_json: str | None = typer.Option(
        None, "--data", "-d", "--config-json", help="Replacement config JSON payload."
    ),
    config_file: Path | None = typer.Option(
        None,
        "--connector-config-file",
        "--file",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Update an auth config in place.

    Use this to rotate an MCP server URL or an OAuth app: deleting and
    recreating the install cascades away every account connected to it, while
    this keeps them. Accounts whose credentials the change invalidates are
    marked for reconnect, and the response says how many.
    """
    from lemma_sdk.openapi_client.models.auth_config_update_schema import (
        AuthConfigUpdateSchema,
    )

    config = read_json(config_json, config_file, required=False) or None
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.auth_configs.update(
            auth_config,
            AuthConfigUpdateSchema.from_dict(
                {
                    "name": name,
                    "status": status,
                    "is_default": is_default,
                    "config": config,
                }
            ),
        ),
    )
    if result is not None:
        emit(state, result)


@auth_configs_app.command("delete")
def delete_auth_config(
    ctx: typer.Context,
    auth_config: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete an auth config."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete auth config {auth_config}?", yes)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.auth_configs.delete(auth_config),
    )
    emit(state, result if result is not None else {"ok": True})


@auth_configs_app.command("refresh-operations")
def refresh_auth_config_operations(
    ctx: typer.Context,
    auth_config: str = typer.Argument(...),
) -> None:
    """Re-discover the operations of an MCP or OpenAPI auth config.

    Run this after the remote server's tools change, or to recover an install
    whose first discovery failed -- deleting and recreating would disconnect
    every account attached to it.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.auth_configs.refresh_operations(auth_config),
    )
    emit(state, result if result is not None else {"ok": True})


@connect_requests_app.command("create")
def create_connect_request(
    ctx: typer.Context,
    connector: str = typer.Argument(...),
    auth_config_id: str | None = typer.Option(None, "--auth-config-id"),
) -> None:
    """Start an account connect request."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.connect_request(
                connector,
                auth_config_id=auth_config_id,
            )
            if hasattr(client.connectors, "connect_request")
            else client.connectors.create_connect_request(
                connector,
                organization_id=org_for(client, s),
                auth_config_id=auth_config_id,
            )
        ),
    )
    if result is not None:
        emit(state, result)


def _search_operations(
    client: Any, state: Any, auth_config: str, query: str | None, limit: int
) -> Any:
    if hasattr(client.connectors, "operations"):
        return client.connectors.operations.search(auth_config, query=query, limit=limit)
    return client.connectors.search_operations(
        auth_config,
        organization_id=org_for(client, state),
        query=query,
        limit=limit,
    )


def _operation_batch(client: Any, state: Any, auth_config: str, names: list[str]) -> Any:
    if hasattr(client.connectors, "operations"):
        return client.connectors.operations.batch(auth_config, names)
    return client.connectors.get_operation_details_batch(
        auth_config,
        organization_id=org_for(client, state),
        operation_names=names,
    )


def _with_input_schemas(client: Any, state: Any, auth_config: str, result: Any) -> Any:
    """Fold each search hit's input schema into the search result.

    Without this, every execution costs three round trips — search to learn the
    id, `get` to learn the payload shape, then execute. The schemas come from one
    extra batch call, so a short result list carries everything needed to run the
    operation.
    """
    payload = to_plain(result)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items:
        return result
    names = [str(item.get("name")) for item in items if item.get("name")]
    if not names:
        return result
    try:
        details = to_plain(_operation_batch(client, state, auth_config, names))
    except Exception:  # noqa: BLE001 — schemas are a bonus, never the point
        return result
    by_name = {
        str(detail.get("name")): detail
        for detail in (details.get("items") or [])
        if isinstance(detail, dict)
    }
    enriched = [
        {
            **item,
            **(
                {"input_schema": by_name[str(item.get("name"))].get("input_schema")}
                if str(item.get("name")) in by_name
                else {}
            ),
        }
        for item in items
    ]
    return {**payload, "items": enriched}


def _search_every_install(
    client: Any, state: Any, query: str | None, limit: int
) -> dict[str, Any]:
    """Search operations across EVERY installed connector, best matches first.

    Naming a connector is an extra round trip the caller often can't make: "which
    operation sends an email?" is answerable without knowing that the install is
    called `workspace-gmail`. Each hit is labelled with the `auth_config` to pass
    to `execute`/`run`, so the answer is directly actionable.

    Costs one request per install, so it is the fallback, not the default path —
    naming a connector (or having only one) still takes exactly one.
    """
    hits: list[dict[str, Any]] = []
    for item in _auth_config_items(client):
        name = str(item.get("name") or item.get("id") or "")
        if not name:
            continue
        try:
            payload = to_plain(_search_operations(client, state, name, query, limit))
        except Exception:  # noqa: BLE001 — one broken install must not sink the search
            continue
        for hit in payload.get("items") or []:
            if isinstance(hit, dict):
                hits.append(
                    {
                        **hit,
                        "auth_config": name,
                        "connector_id": item.get("connector_id") or payload.get("connector_id"),
                    }
                )
    hits.sort(key=lambda hit: float(hit.get("relevance_score") or 0.0), reverse=True)
    return {"items": hits[:limit], "returned_count": min(len(hits), limit)}


@operations_app.command("search")
def search_operations(
    ctx: typer.Context,
    first: Optional[str] = typer.Argument(
        None,
        metavar="[AUTH_CONFIG] [QUERY]",
        help="Auth config/connector, or the search text when only one is given.",
    ),
    search_text: str | None = typer.Argument(None, hidden=True),
    auth_config_opt: str | None = typer.Option(
        None,
        "--auth-config",
        "-c",
        help="Auth config name or connector id. Omit to search every installed connector.",
    ),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(10, "--limit"),
    with_schema: bool | None = typer.Option(
        None,
        "--with-schema/--no-schema",
        help="Include each hit's input schema (default: on for <= 5 results).",
    ),
) -> None:
    """Search operation names and descriptions by intent.

    `lemma connectors operations search "send email"` searches EVERY installed
    connector and labels each hit with the `auth_config` to pass on — you don't
    have to know which connector provides what before you can look. Name a
    connector to scope it (and to make it a single request). Results carry their
    input schema by default when the list is short, so the next step is
    `execute`, not another lookup.
    """

    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        target, text = _split_target_and_rest(
            client, first, search_text, option=auth_config_opt
        )
        text = query or text
        include = with_schema if with_schema is not None else limit <= 5
        if target is None and len(_auth_config_items(client)) > 1:
            # No connector named and several installed: search them all rather
            # than refusing. Schemas are skipped here — they are per-install and
            # the hits span installs; `operations get <auth-config> <op>` or
            # `connectors run` fetches the one you settle on.
            return _search_every_install(client, s, text, limit)
        resolved = _resolve_auth_config(client, target)
        result = _search_operations(client, s, resolved, text, limit)
        return _with_input_schemas(client, s, resolved, result) if include else result

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@operations_app.command("list")
def list_operations(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name or connector id. Auto-discovered when only one is installed.",
    ),
    auth_config_opt: str | None = typer.Option(
        None, "--auth-config", "-c", help="Same as the positional."
    ),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List operation names and descriptions for an installed connector."""

    search_operations(
        ctx,
        first=auth_config,
        search_text=None,
        auth_config_opt=auth_config_opt,
        query=query,
        limit=limit,
        with_schema=False,
    )


@operations_app.command("details")
def operation_details(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name or connector id. Auto-discovered when only one is installed.",
    ),
    operations: list[str] | None = typer.Argument(
        None,
        help="Operation names. Omit to fetch details for every operation.",
    ),
    auth_config_opt: str | None = typer.Option(
        None, "--auth-config", "-c", help="Same as the positional."
    ),
) -> None:
    """Show operation details (input/output schemas) for one or more operations."""
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        # A lone positional that isn't an install is an operation name, not the
        # auth config — same rule as `search`.
        names = list(operations or [])
        target = auth_config_opt or auth_config
        if (
            auth_config_opt is None
            and auth_config is not None
            and not _looks_like_auth_config(client, auth_config)
        ):
            names.insert(0, auth_config)
            target = None
        return _operation_batch(client, s, _resolve_auth_config(client, target), names)

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@operations_app.command("get")
def get_operation(
    ctx: typer.Context,
    first: str = typer.Argument(
        ...,
        metavar="[AUTH_CONFIG] OPERATION",
        help="Auth config/connector, or the operation name when only one is given.",
    ),
    operation: Optional[str] = typer.Argument(None, hidden=True),
    auth_config_opt: str | None = typer.Option(
        None, "--auth-config", "-c", help="Auth config name or connector id."
    ),
) -> None:
    """Show one connector operation, including its input schema.

    The auth config is auto-discovered when only one connector is installed, the
    same as its sibling commands — it used to be the one operations command that
    demanded it explicitly.
    """
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        target, name = _split_target_and_rest(
            client, first, operation, option=auth_config_opt
        )
        if not name:
            raise typer.BadParameter("Give an operation name.", param_hint="OPERATION")
        resolved = _resolve_auth_config(client, target)
        if hasattr(client.connectors, "operations"):
            return client.connectors.operations.get(resolved, name)
        return client.connectors.get_operation_details(
            resolved, name, organization_id=org_for(client, s)
        )

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


def _resolve_account(client: Any, state: Any, account: str | None) -> str | None:
    """Accept an account id OR the email it was connected with.

    An agent reading `connectors overview` sees emails, not ids; making it map
    one to the other by hand is a lookup call for no reason. Only a value that
    looks like an email triggers the lookup — anything else is passed through as
    an id, so this never intercepts an identifier scheme it doesn't recognize.
    """
    if not account or "@" not in account:
        return account
    needle = account.strip().casefold()
    for item in list_items(_list_accounts(client, state, connector=None, limit=200)):
        if str(item.get("email") or "").casefold() == needle:
            return str(item.get("id"))
    raise typer.BadParameter(
        f"No connected account matches '{account}'. See `lemma connectors overview`.",
        param_hint="--account",
    )


def _execute_operation(
    client: Any,
    state: Any,
    *,
    auth_config: str,
    operation: str,
    payload: dict,
    account: str | None,
) -> Any:
    account_id = _resolve_account(client, state, account or payload.get("account_id"))
    body = payload.get("payload", payload)
    if hasattr(client.connectors, "execute"):
        return client.connectors.execute(
            auth_config, operation, payload=body, account_id=account_id
        )
    return client.connectors.execute_operation(
        auth_config,
        operation,
        organization_id=org_for(client, state),
        payload=body,
        account_id=account_id,
    )


@operations_app.command("execute")
def execute_operation(
    ctx: typer.Context,
    first: str = typer.Argument(
        ...,
        metavar="[AUTH_CONFIG] OPERATION",
        help="Auth config/connector, or the operation name when only one is given.",
    ),
    operation: Optional[str] = typer.Argument(None, hidden=True),
    auth_config_opt: str | None = typer.Option(
        None, "--auth-config", "-c", help="Auth config name or connector id."
    ),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload (`-` reads stdin)."
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    account: str | None = typer.Option(
        None, "--account", "--account-id", help="Account id or connected email."
    ),
    metadata_only: bool = typer.Option(
        False,
        "--metadata-only",
        help="Strip large HTML body fields from the response.",
    ),
) -> None:
    """Execute a connector operation.

    The payload goes under a top-level `payload` key, but a bare object is
    accepted too. Operations that take no input need no `--data` at all.
    """
    # Not required: plenty of operations take no input, and demanding `-d '{}'`
    # for those was pure ceremony.
    payload = read_json(json_payload, file, required=False)
    state = state_from_ctx(ctx)

    def run(client, s):  # type: ignore[no-untyped-def]
        target, name = _split_target_and_rest(
            client, first, operation, option=auth_config_opt
        )
        if not name:
            raise typer.BadParameter("Give an operation name.", param_hint="OPERATION")
        return _execute_operation(
            client,
            s,
            auth_config=_resolve_auth_config(client, target),
            operation=name,
            payload=payload,
            account=account,
        )

    result = run_with_client(ctx, run)
    if result is not None:
        if metadata_only:
            result = _strip_body_fields(to_plain(result))
        # Pretty output now renders structured results compact-complete (long body
        # fields fold; pass --full to expand, or --output json to pipe/save).
        emit(state, result)


@triggers_app.command("list")
def list_triggers(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name. Auto-discovered when only one exists.",
    ),
    search: str | None = typer.Option(
        None,
        "--query",
        "-q",
        "--search",
        help="Search trigger descriptions.",
    ),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List connector triggers for an org auth config.

    Only triggers for the auth config's kind are returned (a composio auth
    config returns only COMPOSIO triggers, a LEMMA auth config only LEMMA).
    """

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _list_triggers(
            client,
            s,
            auth_config=auth_config,
            search=search,
            limit=limit,
        ),
    )
    if result is not None:
        emit(state, result)


@triggers_app.command("get")
def get_trigger(
    ctx: typer.Context,
    auth_config: str = typer.Argument(..., help="Auth config name."),
    trigger: str = typer.Argument(..., help="Connector trigger id or event type."),
) -> None:
    """Show one connector trigger for an org auth config."""

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: _get_trigger(client, s, auth_config=auth_config, trigger=trigger),
    )
    if result is not None:
        emit(state, result)


def _account_label(account: dict) -> str:
    who = (
        account.get("email")
        or account.get("provider_account_id")
        or account.get("id")
        or "?"
    )
    status = account.get("status")
    return f"{who} ({status})" if status else str(who)


def _build_overview_rows(configs: list, accounts: list) -> list[dict]:
    """One row per auth config: the app, the name to pass, kind, and accounts."""
    rows: list[dict] = []
    for cfg in configs:
        cfg_id = cfg.get("id")
        app_id = cfg.get("connector_id") or cfg.get("app_id") or ""
        matched = [
            a
            for a in accounts
            if a.get("auth_config_id") == cfg_id
            or (not a.get("auth_config_id") and a.get("connector_id") == app_id)
        ]
        rows.append(
            {
                "app": app_id,
                "auth_config": cfg.get("name") or str(cfg_id or ""),
                "kind": cfg.get("kind") or "",
                "status": cfg.get("status") or "",
                "accounts": ", ".join(_account_label(a) for a in matched) or "(none)",
            }
        )
    return rows


def _render_overview(rows: list[dict]) -> None:
    from rich import box
    from rich.table import Table

    if not rows:
        console.print(
            "[dim]No connectors configured. Install one with "
            "`lemma connectors auth-configs create <app>`.[/dim]"
        )
        return
    view = Table(title="Connectors", box=box.SIMPLE_HEAVY)
    view.add_column("App")
    view.add_column("Auth Config")
    view.add_column("Kind")
    view.add_column("Status")
    view.add_column("Accounts", overflow="fold")
    for row in sorted(rows, key=lambda r: (r["app"], r["kind"])):
        view.add_row(
            row["app"], row["auth_config"], row["kind"], row["status"], row["accounts"]
        )
    console.print(view)
    console.print(
        "[dim]Pass the Auth Config name to operations/triggers, e.g. "
        "`lemma connectors operations search <auth-config> \"<query>\"`.[/dim]"
    )


# Verbs that make an operation change something on the other side. Ranked search
# is lexical, so "list recent emails" happily matches ADD_LABEL_TO_EMAIL — and
# picking that silently is the difference between reading a mailbox and editing
# it. Used to bias resolution and to gate execution of an inferred write.
_MUTATING_OP_TOKENS = frozenset(
    {
        "add", "append", "archive", "assign", "cancel", "create", "delete",
        "disable", "draft", "enable", "forward", "insert", "invite", "label",
        "modify", "move", "patch", "post", "publish", "put", "remove", "rename",
        "reply", "send", "set", "share", "star", "trash", "unarchive", "update",
        "upload", "upsert", "write",
    }
)
_READ_INTENT_TOKENS = frozenset(
    {
        "browse", "check", "download", "fetch", "find", "get", "inspect", "list",
        "load", "look", "read", "recent", "retrieve", "review", "search", "see",
        "show", "summarize", "summarise", "view",
    }
)


def _op_tokens(name: str) -> set[str]:
    return {part for part in name.lower().replace("-", "_").split("_") if part}


def _is_mutating_operation(name: str) -> bool:
    return bool(_op_tokens(name) & _MUTATING_OP_TOKENS)


def _is_read_intent(text: str) -> bool:
    words = {word.strip(".,!?\"'").lower() for word in text.split()}
    return bool(words & _READ_INTENT_TOKENS) and not (words & _MUTATING_OP_TOKENS)


def _required_input_fields(input_schema: Any) -> list[str]:
    if not isinstance(input_schema, dict):
        return []
    required = input_schema.get("required")
    return [str(field) for field in required] if isinstance(required, list) else []


def _resolve_operation(
    client: Any, state: Any, auth_config: str, selector: str
) -> tuple[dict[str, Any], bool]:
    """Turn an operation id OR a plain-English intent into one concrete operation.

    Tries an exact (case-insensitive) id first, then falls back to ranked search.
    Returns ``(detail, inferred)`` — the operation's detail dict, so the caller
    already holds the input schema, plus whether the name was *guessed* from
    natural language rather than given.

    Ranked search is lexical, so a read intent can match a write operation:
    "list recent emails" ranked ADD_LABEL_TO_EMAIL above FETCH_EMAILS. A read
    intent therefore prefers the best NON-mutating hit, and anything inferred
    stays flagged so the caller can refuse to run a write nobody asked for.
    """
    try:
        detail = to_plain(_operation_batch(client, state, auth_config, [selector]))
        items = detail.get("items") or []
        if items:
            return dict(items[0]), False
    except Exception:  # noqa: BLE001 — an unknown id just means "search instead"
        pass

    hits = to_plain(_search_operations(client, state, auth_config, selector, 5)).get(
        "items"
    ) or []
    if not hits:
        raise typer.BadParameter(
            f"No operation on '{auth_config}' matches '{selector}'. Browse them "
            f"with `lemma connectors operations list {auth_config}`.",
            param_hint="OPERATION",
        )
    chosen = hits[0]
    if _is_read_intent(selector):
        chosen = next(
            (
                hit
                for hit in hits
                if not _is_mutating_operation(str(hit.get("name") or ""))
            ),
            hits[0],
        )
    best = str(chosen.get("name"))
    others = ", ".join(
        str(hit.get("name")) for hit in hits if str(hit.get("name")) != best
    )
    typer.echo(
        f"Resolved operation: {selector!r} -> {best}"
        + (f" (also matched: {others})" if others else ""),
        err=True,
    )
    detail = to_plain(_operation_batch(client, state, auth_config, [best]))
    items = detail.get("items") or []
    return (dict(items[0]) if items else {"name": best, "input_schema": {}}), True


@app.command("run")
def run_connector_operation(
    ctx: typer.Context,
    connector: str = typer.Argument(
        ..., help="Connector id (gmail, slack) or an auth-config name."
    ),
    operation: str = typer.Argument(
        ..., help="Operation id, or plain-English intent ('list recent emails')."
    ),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Operation input as JSON (`-` reads stdin)."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    account: str | None = typer.Option(
        None, "--account", "--account-id", help="Account id or connected email."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve and print the operation + input schema without executing.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Run even when the operation was INFERRED from text and mutates data.",
    ),
    metadata_only: bool = typer.Option(
        False,
        "--metadata-only",
        help="Strip large HTML body fields from the response.",
    ),
) -> None:
    """Run a connector operation in ONE call: resolve, check, execute.

    Doing anything through a connector used to take four commands — `overview`
    to learn the auth-config name, `operations search` to learn the operation id,
    `operations get` to learn the payload shape, then `execute`. This collapses
    that into one, and never guesses silently: the resolved connector, operation,
    and account are printed, so the next run can name them exactly.

    Omit `--data` on an operation that needs input and it prints the input schema
    instead of failing — the same thing `--dry-run` does on purpose.

        lemma connectors run gmail "list recent emails" --dry-run
        lemma connectors run gmail gmail_list_messages -d '{"max_results": 5}'
    """
    payload = read_json(json_payload, file, required=False)
    state = state_from_ctx(ctx)
    gave_payload = json_payload is not None or file is not None

    def run(client, s):  # type: ignore[no-untyped-def]
        auth_config = _resolve_auth_config(client, connector)
        detail, inferred = _resolve_operation(client, s, auth_config, operation)
        name = str(detail.get("name") or operation)
        input_schema = detail.get("input_schema") or {}
        required = _required_input_fields(input_schema)

        # Never run a data-changing operation nobody actually named. Ranked
        # search matched a write op for a read intent in real use, and the only
        # thing that stopped it was a required field it happened to have.
        if inferred and not dry_run and _is_mutating_operation(name) and not yes:
            raise typer.BadParameter(
                f"'{operation}' resolved to {name}, which CHANGES data. Refusing "
                "to run an operation inferred from text. Name it explicitly "
                f"(`lemma connectors run {connector} {name} ...`), inspect it "
                "first with --dry-run, or pass --yes.",
                param_hint="OPERATION",
            )

        if dry_run or (required and not gave_payload):
            if inferred and _is_mutating_operation(name):
                typer.echo(
                    f"WARNING: {name} changes data — it was inferred from "
                    f"{operation!r}, not named.",
                    err=True,
                )
            if required and not gave_payload and not dry_run:
                typer.echo(
                    f"{name} needs input: {', '.join(required)}", err=True
                )
            typer.echo(
                f"next: lemma connectors run {connector} {name} -d '{{...}}'",
                err=True,
            )
            return {
                "auth_config": auth_config,
                "operation": name,
                "input_schema": input_schema,
                "required": required,
                "executed": False,
            }

        result = _execute_operation(
            client,
            s,
            auth_config=auth_config,
            operation=name,
            payload=payload,
            account=account,
        )
        typer.echo(
            f"Ran {auth_config} / {name}"
            + (f" (account {account})" if account else ""),
            err=True,
        )
        return result

    result = run_with_client(ctx, run)
    if result is None:
        return
    if metadata_only:
        result = _strip_body_fields(to_plain(result))
    emit(state, result)


@app.command("overview")
def connectors_overview(ctx: typer.Context) -> None:
    """Show every configured app: auth-config name, kind, and connected accounts.

    Operations and triggers are addressed by AUTH-CONFIG NAME (and differ per
    kind), so this is the one place to find the exact name to pass to
    `operations` and `triggers`.
    """
    state = state_from_ctx(ctx)

    def fetch(client, s):  # type: ignore[no-untyped-def]
        configs = client.connectors.auth_configs.list(limit=200)
        accounts = _list_accounts(client, s, connector=None, limit=200)
        return {"configs": to_plain(configs), "accounts": to_plain(accounts)}

    result = run_with_client(ctx, fetch)
    if result is None:
        return
    rows = _build_overview_rows(
        list_items(result["configs"]), list_items(result["accounts"])
    )
    if state.output == "json":
        emit(state, {"items": rows})
        return
    _render_overview(rows)


@app.command("status")
def connector_status(
    ctx: typer.Context,
) -> None:
    """Show installed apps and connected accounts in one view."""

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.connectors.status(),
    )
    if result is None:
        return

    data = to_plain(result) if not isinstance(result, dict) else result

    if state.output == "pretty":
        installed = data.get("installed", []) if isinstance(data, dict) else []
        accounts = data.get("accounts", []) if isinstance(data, dict) else []

        if installed:
            typer.echo("Installed apps:")
            for item in installed:
                name = item.get("name") or item.get("connector_id") or "?"
                title = item.get("title") or ""
                status = item.get("status") or ""
                kind = item.get("kind") or ""
                typer.echo(f"  {name:<20} {title:<20} {status:<10} {kind}")
        else:
            typer.echo("Installed apps: (none)")

        typer.echo("")

        if accounts:
            typer.echo("Your connected accounts:")
            for item in accounts:
                app_id = item.get("connector_id") or "?"
                title = item.get("title") or ""
                email = item.get("email") or ""
                status = item.get("status") or ""
                typer.echo(f"  {app_id:<20} {title:<20} {email:<30} {status}")
        else:
            typer.echo("Your connected accounts: (none)")
    else:
        emit(state, result)


def _resolve_kind_for_app(client: Any, connector: str) -> str | None:
    """Look up the installed auth config for this app and return its kind (lowercase)."""
    try:
        raw = client.connectors.auth_configs.list(limit=50)
        data = to_plain(raw)
        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items", [])
        for item in items:
            app_id = item.get("connector_id") or item.get("app_id") or ""
            if app_id == connector:
                kind = item.get("kind") or ""
                return kind.lower() if kind else None
    except Exception:
        pass
    return None


@app.command("describe")
def describe_connector(
    ctx: typer.Context,
    connector: str = typer.Argument(..., help="Connector ID (e.g. gmail, slack)."),
    kind: Optional[str] = typer.Option(
        None,
        "--kind",
        help="Override kind, e.g. package or composio. Auto-detected from the installed auth config when omitted.",
    ),
) -> None:
    """Show the skill guide for a connector app.

    Automatically selects the kind-specific skill when the app ships as both a
    vendored package and a Composio toolkit and an auth config is installed.
    """

    state = state_from_ctx(ctx)

    def _fetch(client: Any, _s: Any) -> Any:
        effective_kind = kind or _resolve_kind_for_app(client, connector)
        return client.connectors.apps.skill(connector, kind=effective_kind)

    result = run_with_client(ctx, _fetch)
    if result is None:
        return

    data = to_plain(result) if not isinstance(result, dict) else result

    if state.output == "pretty":
        markdown = data.get("markdown", "") if isinstance(data, dict) else str(data)
        try:
            from rich.console import Console
            from rich.markdown import Markdown
            Console().print(Markdown(markdown))
        except ImportError:
            typer.echo(markdown)
    else:
        emit(state, result)


app.add_typer(auth_configs_app, name="auth-configs")
app.add_typer(accounts_app, name="accounts")
app.add_typer(connect_requests_app, name="connect-requests")
app.add_typer(operations_app, name="operations")
app.add_typer(triggers_app, name="triggers")
