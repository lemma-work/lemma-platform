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


def _resolve_auth_config(client: Any, auth_config: str | None) -> str:
    """Auto-discover auth config when not provided. Uses the sole config if only one exists."""
    if auth_config is not None:
        return auth_config
    raw = client.connectors.auth_configs.list(limit=50)
    data = to_plain(raw)
    items: list[Any] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items", [])
    if len(items) == 1:
        name = str(items[0].get("name") or items[0].get("id") or "")
        typer.echo(f"Using auth config: {name}", err=True)
        return name
    if not items:
        raise typer.BadParameter(
            "No auth configs found. See `lemma connectors overview` or install one "
            "with `lemma connectors auth-configs create <app>`.",
            param_hint="AUTH_CONFIG",
        )
    # Operations/triggers differ per provider, so the name (which encodes the
    # provider choice) must be explicit. Show provider to make the choice obvious.
    names = ", ".join(
        f"{i.get('name') or i.get('id')} ({i.get('provider') or '?'})" for i in items
    )
    raise typer.BadParameter(
        f"Multiple auth configs — specify one (see `lemma connectors overview`): {names}",
        param_hint="AUTH_CONFIG",
    )


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
    provider: str = typer.Option("LEMMA", "--provider"),
    config_source: str = typer.Option("SYSTEM_DEFAULT", "--config-source"),
    credential_json: str | None = typer.Option(
        None, "--data", "-d", "--credential-json", help="Credential config JSON payload."
    ),
    credential_file: Path | None = typer.Option(
        None,
        "--credential-file",
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
                        "provider": provider,
                        "config_source": config_source,
                        "credential_config": credential_config,
                    }
                )
            )
            if hasattr(client.connectors, "auth_configs")
            else client.connectors.create_auth_config(
                organization_id=org_for(client, s),
                connector_id=connector,
                name=name,
                provider=provider,
                config_source=config_source,
                credential_config=credential_config,
            )
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


@operations_app.command("search")
def search_operations(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name. Auto-discovered when only one exists.",
    ),
    search_text: str | None = typer.Argument(
        None,
        help="Natural-language search text or operation id.",
    ),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Search operation names and descriptions."""

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.operations.search(
                _resolve_auth_config(client, auth_config),
                query=query or search_text,
                limit=limit,
            )
            if hasattr(client.connectors, "operations")
            else client.connectors.search_operations(
                _resolve_auth_config(client, auth_config),
                organization_id=org_for(client, s),
                query=query or search_text,
                limit=limit,
            )
        ),
    )
    if result is not None:
        emit(state, result)


@operations_app.command("list")
def list_operations(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name. Auto-discovered when only one exists.",
    ),
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List operation names and descriptions for an org auth config."""

    search_operations(ctx, auth_config=auth_config, search_text=None, query=query, limit=limit)


@operations_app.command("details")
def operation_details(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name. Auto-discovered when only one exists.",
    ),
    operations: list[str] | None = typer.Argument(
        None,
        help="Operation names. Omit to fetch details for every operation.",
    ),
) -> None:
    """Show operation details for a connector."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.operations.batch(
                _resolve_auth_config(client, auth_config),
                operations or [],
            )
            if hasattr(client.connectors, "operations")
            else client.connectors.get_operation_details_batch(
                _resolve_auth_config(client, auth_config),
                organization_id=org_for(client, s),
                operation_names=operations or [],
            )
        ),
    )
    if result is not None:
        emit(state, result)


@operations_app.command("get")
def get_operation(
    ctx: typer.Context,
    auth_config: str = typer.Argument(
        ...,
        help="Auth config name.",
    ),
    operation: str = typer.Argument(..., help="Operation name."),
) -> None:
    """Show one connector operation."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.operations.get(auth_config, operation)
            if hasattr(client.connectors, "operations")
            else client.connectors.get_operation_details(
                auth_config,
                operation,
                organization_id=org_for(client, s),
            )
        ),
    )
    if result is not None:
        emit(state, result)


@operations_app.command("execute")
def execute_operation(
    ctx: typer.Context,
    auth_config: Optional[str] = typer.Argument(
        None,
        help="Auth config name. Auto-discovered when only one exists.",
    ),
    operation: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    account: str | None = typer.Option(None, "--account", "--account-id"),
    metadata_only: bool = typer.Option(
        False,
        "--metadata-only",
        help="Strip large HTML body fields from the response.",
    ),
) -> None:
    """Execute a connector operation."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: (
            client.connectors.execute(
                _resolve_auth_config(client, auth_config),
                operation,
                payload=payload.get("payload", payload),
                account_id=account or payload.get("account_id"),
            )
            if hasattr(client.connectors, "execute")
            else client.connectors.execute_operation(
                _resolve_auth_config(client, auth_config),
                operation,
                organization_id=org_for(client, s),
                payload=payload.get("payload", payload),
                account_id=account or payload.get("account_id"),
            )
        ),
    )
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

    Only triggers for the auth config's provider are returned (a COMPOSIO auth
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
    """One row per auth config: the app, the name to pass, provider, and accounts."""
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
                "provider": cfg.get("provider") or "",
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
    view.add_column("Provider")
    view.add_column("Status")
    view.add_column("Accounts", overflow="fold")
    for row in sorted(rows, key=lambda r: (r["app"], r["provider"])):
        view.add_row(
            row["app"], row["auth_config"], row["provider"], row["status"], row["accounts"]
        )
    console.print(view)
    console.print(
        "[dim]Pass the Auth Config name to operations/triggers, e.g. "
        "`lemma connectors operations search <auth-config> \"<query>\"`.[/dim]"
    )


@app.command("overview")
def connectors_overview(ctx: typer.Context) -> None:
    """Show every configured app: auth-config name, provider, and connected accounts.

    Operations and triggers are addressed by AUTH-CONFIG NAME (and differ per
    provider — LEMMA vs COMPOSIO), so this is the one place to find the exact
    name to pass to `operations` and `triggers`.
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
                provider = item.get("provider") or ""
                typer.echo(f"  {name:<20} {title:<20} {status:<10} {provider}")
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


def _resolve_provider_for_app(client: Any, connector: str) -> str | None:
    """Look up the installed auth config for this app and return its provider (lowercase)."""
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
                provider = item.get("provider") or ""
                return provider.lower() if provider else None
    except Exception:
        pass
    return None


@app.command("describe")
def describe_connector(
    ctx: typer.Context,
    connector: str = typer.Argument(..., help="Connector ID (e.g. gmail, slack)."),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="Override provider: lemma or composio. Auto-detected from installed auth config when omitted.",
    ),
) -> None:
    """Show the skill guide for a connector app.

    Automatically selects the provider-specific skill when the app supports both
    LEMMA and Composio providers and an auth config is installed for this org.
    """

    state = state_from_ctx(ctx)

    def _fetch(client: Any, _s: Any) -> Any:
        effective_provider = provider or _resolve_provider_for_app(client, connector)
        return client.connectors.apps.skill(connector, provider=effective_provider)

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
