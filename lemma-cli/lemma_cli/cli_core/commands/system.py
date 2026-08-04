from __future__ import annotations

import os
from typing import Any

import typer

from lemma_sdk.auth import LoginTimeoutError, run_login_flow
from lemma_sdk.config import (
    DEFAULT_SERVER_NAME,
    ENV_SERVER_NAME,
    get_server_config,
    get_access_token_from_config,
    mask_token,
    normalize_server_config,
    normalize_server_name,
    server_names,
    put_server_config,
    resolve_base_url,
    resolve_auth_url,
    resolve_token,
    resolve_verify_ssl,
    save_config,
    upsert_auth_session,
)
from ..context import (
    remember_org,
    remember_pod,
    resolve_org,
    resolve_pod,
    selected_conversation,
    selected_org,
    selected_pod,
)
from ..servers import upsert_server
from ..confirm import confirm_destructive
from ..io import emit
from ..io import list_items
from ..select import item_label, select_from_items
from ..state import (
    clear_auth,
    fail,
    refresh_auth_session,
    resolved_auth_urls,
    run_with_client,
    state_from_ctx,
)

auth_app = typer.Typer(help="Authentication commands.")
config_app = typer.Typer(
    help="CLI context and per-server defaults (pod/org).",
    invoke_without_command=True,
    no_args_is_help=False,
)
server_app = typer.Typer(
    help="Show and manage Lemma CLI servers.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@auth_app.command("login")
def login(
    ctx: typer.Context,
    init_defaults: bool = typer.Option(
        True,
        "--init/--no-init",
        help="Select default org and pod after login.",
    ),
) -> None:
    """Log in via the browser and store the session."""
    state = state_from_ctx(ctx)
    if state.server_read_only:
        fail(
            "The env server is read-only. Unset LEMMA_TOKEN before running browser login."
        )
    try:
        base_url, auth_url = resolved_auth_urls(state)
        result = run_login_flow(
            base_url=base_url,
            auth_url=auth_url,
            verify_ssl=resolve_verify_ssl(state.no_verify_ssl),
            timeout=state.timeout,
        )
        session = dict(result.session)
        session["auth_url"] = auth_url
        state.config = upsert_auth_session(state.config, session)
        state.config.setdefault("base_url", base_url)
        state.config["auth_url"] = auth_url
        if state.root_config is not None:
            state.root_config = put_server_config(
                state.root_config, state.server, state.config
            )
            save_config(state.config_path, state.root_config)
        else:
            save_config(state.config_path, state.config)
        emit(
            state,
            {"ok": True, "email": session.get("email"), "login_url": result.login_url},
        )
        if init_defaults:
            _run_init_flow(ctx, prompt=True)
    except (LoginTimeoutError, ValueError, OSError) as exc:
        fail(str(exc))


@auth_app.command("status")
def status(ctx: typer.Context) -> None:
    """Show the authenticated user profile."""
    result = run_with_client(ctx, lambda client, _state: client.user.profile())
    if result is not None:
        emit(state_from_ctx(ctx), result)


@auth_app.command("logout")
def logout(ctx: typer.Context) -> None:
    """Clear the stored session for the active server."""
    state = state_from_ctx(ctx)
    clear_auth(state)
    emit(state, {"ok": True, "path": str(state.config_path)})


def _access_token_expired(token: str, *, skew_seconds: int = 30) -> bool:
    """True if the JWT's ``exp`` is in the past (within a small skew). Opaque or
    undecodable tokens are treated as not-expired so we never force a refresh on
    something we cannot read."""
    import base64
    import json
    import time

    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return time.time() >= (exp - skew_seconds)


@auth_app.command("print-token")
def print_token(
    ctx: typer.Context,
    refresh: bool = typer.Option(
        False, "--refresh", help="Force a token refresh before printing."
    ),
) -> None:
    """Print the current access token to stdout (refreshing if it has expired).

    Outputs the raw token only — no formatting — so it can be captured by tooling
    (e.g. an app dev server seeding ``localStorage`` to log the browser in). On a
    laptop the CLI refreshes via the stored refresh token; inside an agent box it
    returns the injected ``LEMMA_TOKEN``.
    """
    state = state_from_ctx(ctx)
    use_env = state.server_source == "env"
    try:
        token = resolve_token(state.token, state.config, use_env=use_env)
    except ValueError as exc:
        fail(str(exc))
        return

    if refresh or _access_token_expired(token):
        # refresh_auth_session is a no-op for env-server / explicit-token modes and
        # only acts when a stored refresh token is present, so this is safe to call
        # unconditionally. Keep the existing token if the refresh attempt fails.
        try:
            if refresh_auth_session(state):
                token = resolve_token(state.token, state.config, use_env=use_env)
        except Exception:
            pass

    typer.echo(token)


@config_app.callback()
def config_root(ctx: typer.Context) -> None:
    """Show the resolved CLI context when run with no subcommand.

    `config` is the home for per-server defaults (default pod/org). To change the
    active pod/org for just this shell use `lemma pods/orgs select` (session-only);
    to change the persistent default use `config set-default-pod/org`. Server
    *connections* (URLs, token) live under `lemma servers`.
    """
    if ctx.invoked_subcommand is None:
        show(ctx)


@config_app.command("show")
def show(ctx: typer.Context) -> None:
    """Show the local CLI configuration."""
    state = state_from_ctx(ctx)
    auth = (
        state.config.get("auth") if isinstance(state.config.get("auth"), dict) else {}
    )
    emit(
        state,
        {
            "path": str(state.config_path),
            "server": state.server,
            "active_server": state.server,
            "servers": server_names(state.root_config or {})
            if state.root_config is not None
            else [DEFAULT_SERVER_NAME],
            "base_url": state.config.get("base_url"),
            "auth_url": state.config.get("auth_url"),
            "token": mask_token(state.config.get("token")),
            "defaults": state.config.get("defaults", {}),
            "resolved": {
                "org_id": selected_org(state, required=False),
                "pod_id": selected_pod(state, required=False),
                "conversation_id": selected_conversation(state, required=False),
            },
            "env": {
                "LEMMA_ORG_ID": os.getenv("LEMMA_ORG_ID"),
                "LEMMA_POD_ID": os.getenv("LEMMA_POD_ID"),
                "LEMMA_CONVERSATION_ID": os.getenv("LEMMA_CONVERSATION_ID"),
                "LEMMA_TOKEN": mask_token(os.getenv("LEMMA_TOKEN")),
                "LEMMA_SERVER": os.getenv("LEMMA_SERVER"),
            },
            "project_env": state.project_env,
            "auth": {"email": auth.get("email"), "user_id": auth.get("user_id")}
            if auth
            else None,
        },
    )


@config_app.command("set-default-pod")
def set_default_pod_cmd(
    ctx: typer.Context,
    pod_id: str = typer.Argument(..., help="Pod id to persist as this server's default."),
) -> None:
    """Persist the default pod for the active server (seeds new shells).

    For a one-off session change use `lemma pods select` instead. Tip:
    `lemma pods select <name> --save-default` resolves a name and persists in one step.
    """
    state = state_from_ctx(ctx)
    remember_pod(state, pod_id)
    emit(state, {"ok": True, "server": state.server, "default_pod_id": pod_id})


@config_app.command("set-default-org")
def set_default_org_cmd(
    ctx: typer.Context,
    org_id: str = typer.Argument(..., help="Organization id to persist as this server's default."),
    clear_pod: bool = typer.Option(
        True, "--clear-pod/--keep-pod", help="Clear the saved default pod too."
    ),
) -> None:
    """Persist the default organization for the active server (seeds new shells)."""
    state = state_from_ctx(ctx)
    remember_org(state, org_id, clear_pod=clear_pod)
    emit(state, {"ok": True, "server": state.server, "default_org_id": org_id})


@server_app.command("set")
def set_server_config(
    ctx: typer.Context,
    server: str | None = typer.Option(None, "--server"),
    base_url: str | None = typer.Option(None, "--base-url"),
    auth_url: str | None = typer.Option(None, "--auth-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    """Set the base URL, auth URL, or token for a server connection."""
    state = state_from_ctx(ctx)
    if not any([base_url, auth_url, token]):
        fail("Provide --base-url, --auth-url, or --token.")
    if state.server_read_only and not server:
        fail("The env server is read-only. Pass --server to update a stored server.")
    if server:
        _switch_state_server(state, server, create=True)
    if base_url:
        state.config["base_url"] = base_url
    if auth_url:
        state.config["auth_url"] = auth_url
    if token:
        state.config["token"] = token
    if state.root_config is not None:
        state.root_config = put_server_config(
            state.root_config, state.server, state.config
        )
        save_config(state.config_path, state.root_config)
    else:
        save_config(state.config_path, state.config)
    emit(state, {"ok": True, "path": str(state.config_path)})


@server_app.callback()
def server_root(ctx: typer.Context) -> None:
    """List configured servers when run without a subcommand.

    `lemma-cloud` ships as the default; the lemma-stack installer adds `local`. Bare
    `lemma servers` is read-only (it shows your servers); use `lemma servers select`
    to switch the active one, `lemma servers create <name> --base-url … --auth-url …`
    to add another, and `lemma servers set` to edit one.
    """
    if ctx.invoked_subcommand is None:
        list_servers(ctx)


@server_app.command("show")
def server_show(
    ctx: typer.Context,
    resolve: bool = typer.Option(
        True,
        "--resolve/--no-resolve",
        help="Fetch current user/org/pod details when authenticated.",
    ),
) -> None:
    """Show the active server and resolved settings."""
    state = state_from_ctx(ctx)
    payload: dict[str, Any] = {
        "server": state.server,
        "source": state.server_source,
        "read_only": state.server_read_only,
        "path": None if state.server == ENV_SERVER_NAME else str(state.config_path),
        "base_url": _setting_with_source(
            state,
            "base_url",
            state.base_url,
            "flag:--base-url",
            default=resolve_base_url(None, {}, use_env=False),
        ),
        "auth_url": _setting_with_source(
            state,
            "auth_url",
            state.auth_url,
            "flag:--auth-url",
            default=resolve_auth_url(None, {}, use_env=False),
        ),
        "token": _setting_with_source(
            state,
            "token",
            state.token,
            "flag:--token",
            mask=True,
        ),
        "org_id": _default_with_source(state, "org_id", "flag:--org"),
        "pod_id": _default_with_source(state, "pod_id", "flag:--pod"),
        "conversation_id": _default_with_source(
            state, "conversation_id", "flag:--conversation-id"
        ),
        "auth": {
            "email": (state.config.get("auth") or {}).get("email")
            if isinstance(state.config.get("auth"), dict)
            else None,
            "user_id": (state.config.get("auth") or {}).get("user_id")
            if isinstance(state.config.get("auth"), dict)
            else None,
        },
        "env_server_active": state.server == ENV_SERVER_NAME,
    }
    if not resolve or not get_access_token_from_config(state.config):
        emit(state, payload)
        return

    def load_remote(client, s):  # type: ignore[no-untyped-def]
        result = dict(payload)
        result["current_user"] = client.user.profile()
        org_value = selected_org(s, required=False)
        pod_value = selected_pod(s, required=False)
        if org_value:
            result["selected_org"] = resolve_org(client, org_value)
        if pod_value:
            result["selected_pod"] = resolve_pod(client, s, pod_value, org=org_value)
        return result

    result = run_with_client(ctx, load_remote)
    if result is not None:
        emit(state, result)


def _server_items(state, root) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    items = []
    if state.server == ENV_SERVER_NAME:
        items.append(
            {
                "id": ENV_SERVER_NAME,
                "name": ENV_SERVER_NAME,
                "active": True,
                "source": "env",
                "read_only": True,
                "base_url": resolve_base_url(None, state.config, use_env=True),
                "auth_url": resolve_auth_url(None, state.config, use_env=True),
                "org_id": (state.config.get("defaults") or {}).get("org_id"),
                "pod_id": (state.config.get("defaults") or {}).get("pod_id"),
                "email": None,
            }
        )
    for name in server_names(root):
        server = get_server_config(root, name)
        items.append(
            {
                "id": name,
                "name": name,
                "active": name == state.server,
                "source": "config",
                "read_only": False,
                "base_url": resolve_base_url(None, server, use_env=False),
                "auth_url": resolve_auth_url(None, server, use_env=False),
                "org_id": (server.get("defaults") or {}).get("org_id"),
                "pod_id": (server.get("defaults") or {}).get("pod_id"),
                "email": (server.get("auth") or {}).get("email")
                if isinstance(server.get("auth"), dict)
                else None,
            }
        )
    return items


@server_app.command("list")
def list_servers(ctx: typer.Context) -> None:
    """List stored servers."""
    state = state_from_ctx(ctx)
    items = _server_items(state, state.root_config or {})
    emit(
        state,
        {
            "active_server": state.server,
            "servers": items,
        },
    )


@server_app.command("create")
def create_server(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    base_url: str | None = typer.Option(None, "--base-url", help="Backend API base URL."),
    auth_url: str | None = typer.Option(None, "--auth-url", help="Frontend/auth URL."),
    token: str | None = typer.Option(None, "--token"),
    copy_current: bool = typer.Option(
        False, "--copy-current", help="Start from the current server config."
    ),
    use: bool = typer.Option(False, "--use/--no-use", help="Switch to this server."),
) -> None:
    """Create or update a stored server."""
    state = state_from_ctx(ctx)
    _ensure_root_config(state)
    server_name = normalize_server_name(name)
    if server_name == ENV_SERVER_NAME:
        fail("The env server is reserved for LEMMA_* environment variables.")
    emit(
        state,
        {
            "server": upsert_server(
                state,
                name=server_name,
                base_url=base_url,
                auth_url=auth_url,
                token=token,
                copy_current=copy_current,
                make_active=use,
            )
        },
    )


@server_app.command("select")
def select_server(
    ctx: typer.Context,
    name: str | None = typer.Argument(None),
) -> None:
    """Switch the active server. Opens a picker when no name is given."""
    state = state_from_ctx(ctx)
    root = _ensure_root_config(state)
    if name is None:
        items = _server_items(state, root)
        chosen = select_from_items(items, label="server", current_id=state.server)
        name = str(chosen.get("name"))
    server_name = normalize_server_name(name)
    if server_name == ENV_SERVER_NAME:
        fail("The env server is selected automatically when LEMMA_TOKEN is set.")
    servers = root.get("servers") or {}
    if server_name not in servers:
        fail(f"Server not found: {server_name}")
    root["active_server"] = server_name
    state.server = server_name
    state.config = get_server_config(root, server_name)
    state.root_config = root
    save_config(state.config_path, root)
    emit(state, {"active_server": server_name})


@server_app.command("delete")
def delete_server(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a stored server."""
    state = state_from_ctx(ctx)
    root = _ensure_root_config(state)
    server_name = normalize_server_name(name)
    if server_name == ENV_SERVER_NAME:
        fail("The env server is not stored and cannot be deleted.")
    if server_name == DEFAULT_SERVER_NAME:
        fail(f"The {DEFAULT_SERVER_NAME} server cannot be deleted.")
    servers = root.get("servers") or {}
    if server_name not in servers:
        fail(f"Server not found: {server_name}")
    confirm_destructive(f"Delete server {server_name}?", yes)
    servers.pop(server_name)
    if root.get("active_server") == server_name:
        root["active_server"] = DEFAULT_SERVER_NAME
        state.server = DEFAULT_SERVER_NAME
        state.config = get_server_config(root, DEFAULT_SERVER_NAME)
    save_config(state.config_path, root)
    emit(state, {"deleted": server_name, "active_server": root["active_server"]})


@server_app.command("init")
def server_init(
    ctx: typer.Context,
    org: str | None = typer.Option(None, "--org"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Select default org and pod for the active server."""
    _run_init_flow(ctx, org=org, pod=pod, prompt=True)


def _fetch_server_api_version(state) -> tuple[str | None, str | None]:  # type: ignore[no-untyped-def]
    """Return (server_api_version, error). Reads info.version from /openapi.json."""
    import json
    import ssl
    import urllib.request

    base_url, _auth_url = resolved_auth_urls(state)
    url = base_url.rstrip("/") + "/openapi.json"
    context = None
    if not resolve_verify_ssl(state.no_verify_ssl):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=state.timeout, context=context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("info", {}).get("version") or "") or None, None
    except Exception as exc:  # network/parse errors are diagnostics, not fatal
        return None, str(exc)


def run_feedback(
    ctx: typer.Context,
    *,
    category: str,
    subject: str,
    issue_encountered: str,
    expected_behavior: str,
    actual_behavior: str,
    suggested_next_steps: str | None,
) -> None:
    """Send product feedback.

    This was `lemma tools report-feedback`, behind a two-command `tools` group
    that wrapped exactly two unrelated things and read like a namespace worth
    exploring. Reporting a problem is a first-class action, so it is a
    first-class command.
    """
    from ..state import run_with_client

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.tools.report_feedback(
            category=category,
            subject=subject,
            issue_encountered=issue_encountered,
            expected_behavior=expected_behavior,
            actual_behavior=actual_behavior,
            suggested_next_steps=suggested_next_steps,
        ),
    )
    if result is not None:
        emit(state, result)


def run_version(ctx: typer.Context) -> None:
    """Show CLI, SDK, and bundled API-schema versions."""
    from ..state import console
    from ..versions import local_version_payload, version_lines

    state = state_from_ctx(ctx)
    if state.output == "json":
        emit(state, local_version_payload())
        return
    for line in version_lines():
        console.print(line)


def run_doctor(ctx: typer.Context) -> None:
    """Diagnose version skew and duplicate installs."""
    from ..io import emit as _emit
    from ..state import console
    from ..versions import (
        bundled_api_version,
        cli_version,
        lemma_executables,
        sdk_dist_version,
    )

    state = state_from_ctx(ctx)
    base_url, _auth_url = resolved_auth_urls(state)
    bundled = bundled_api_version()
    server_version, server_error = _fetch_server_api_version(state)
    installs = lemma_executables()

    if server_error is not None:
        skew = "server_unreachable"
    elif bundled is None:
        skew = "unknown"
    elif server_version == bundled:
        skew = "in_sync"
    else:
        skew = "version_mismatch"

    payload: dict[str, Any] = {
        "lemma_cli": cli_version(),
        "lemma_sdk": sdk_dist_version(),
        "bundled_api_schema": bundled or "unknown",
        "server": base_url,
        "server_api_schema": server_version or "unknown",
        "skew": skew,
        "lemma_installs": installs,
    }

    if state.output == "json":
        _emit(state, payload)
        return

    console.print(f"[bold]lemma[/bold] {payload['lemma_cli']}    "
                  f"[bold]lemma-sdk[/bold] {payload['lemma_sdk']}")
    console.print(f"[dim]bundled API schema[/dim] {payload['bundled_api_schema']}")
    console.print(f"[dim]server[/dim] {base_url}")

    if skew == "in_sync":
        console.print(f"[green]✓ in sync[/green] — server API schema "
                      f"{server_version} matches the SDK")
    elif skew == "version_mismatch":
        console.print(
            f"[yellow]⚠ skew[/yellow] — SDK built against {bundled} but server "
            f"reports {server_version}.\n"
            "  If the server is newer: regenerate + reinstall the SDK "
            "(bash lemma-python/scripts/generate_openapi_client.sh && "
            "uv tool install --force --editable lemma-cli).\n"
            "  If the SDK is newer: the server deployment is behind."
        )
    elif skew == "server_unreachable":
        console.print(f"[dim]server API schema unavailable: {server_error}[/dim]")
    else:
        console.print("[dim]bundled API schema unknown (SDK missing _spec_info)[/dim]")

    if len(installs) > 1:
        console.print(
            "[yellow]⚠ multiple lemma installs on PATH[/yellow] — commands may "
            "resolve to different versions:")
        for path in installs:
            console.print(f"    {path}")
        console.print("  Keep one global install: "
                      "[bold]uv tool install --force --editable lemma-cli[/bold]")
    elif installs:
        console.print(f"[green]✓ single install[/green] {installs[0]}")


def _ensure_root_config(state) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if state.root_config is None:
        root, server = normalize_server_config(
            state.config, selected_server=state.server
        )
        state.root_config = root
        state.server = server
        state.config = get_server_config(root, server)
    return state.root_config


def _switch_state_server(
    state, server: str, *, create: bool = False  # type: ignore[no-untyped-def]
) -> None:
    root = _ensure_root_config(state)
    server_name = normalize_server_name(server)
    if server_name == ENV_SERVER_NAME:
        fail("The env server is reserved for LEMMA_* environment variables.")
    servers = root.setdefault("servers", {})
    if server_name not in servers and not create:
        fail(f"Server not found: {server_name}")
    if server_name not in servers:
        servers[server_name] = {"defaults": {}}
    state.server = server_name
    state.config = get_server_config(root, server_name)
    root["active_server"] = server_name
    state.server_source = "config"
    state.server_read_only = False


def _setting_with_source(
    state,  # type: ignore[no-untyped-def]
    key: str,
    explicit: str | None,
    explicit_source: str,
    *,
    default: str | None = None,
    mask: bool = False,
) -> dict[str, Any]:
    sources = (
        state.config.get("_sources")
        if isinstance(state.config.get("_sources"), dict)
        else {}
    )
    if explicit:
        return {
            "value": mask_token(explicit) if mask else explicit,
            "source": explicit_source,
        }
    value = state.config.get(key)
    if value:
        return {
            "value": mask_token(value) if mask else value,
            "source": f"env:{sources[key]}" if key in sources else "server",
        }
    return {"value": default, "source": "default" if default is not None else None}


def _default_with_source(
    state, key: str, explicit_source: str  # type: ignore[no-untyped-def]
) -> dict[str, Any]:
    runtime = (
        state.config.get("_runtime")
        if isinstance(state.config.get("_runtime"), dict)
        else {}
    )
    runtime_key = {
        "org_id": "org",
        "pod_id": "pod",
        "conversation_id": "conversation",
    }[key]
    if runtime.get(runtime_key):
        return {"value": runtime[runtime_key], "source": explicit_source}
    defaults = (
        state.config.get("defaults")
        if isinstance(state.config.get("defaults"), dict)
        else {}
    )
    value = defaults.get(key)
    sources = (
        state.config.get("_sources")
        if isinstance(state.config.get("_sources"), dict)
        else {}
    )
    return {
        "value": value,
        "source": f"env:{sources[key]}"
        if key in sources
        else ("server" if value else None),
    }


def _configure_defaults(
    client,  # type: ignore[no-untyped-def]
    state,
    *,
    org: str | None = None,
    pod: str | None = None,
    prompt: bool = True,
) -> dict[str, Any]:
    orgs_api = getattr(client, "orgs", None) or client.organizations
    org_item = resolve_org(client, org) if org else None
    if org_item is None:
        orgs = list_items(orgs_api.list(limit=200))
        org_item = (
            select_from_items(orgs, label="organization")
            if prompt
            else (orgs[0] if orgs else None)
        )
        if org_item is None:
            fail("No organizations found.")

    org_id = str(org_item.get("id"))
    pod_item = resolve_pod(client, state, pod, org=org_id) if pod else None
    if pod_item is None:
        pods_api = client.pods
        if hasattr(pods_api, "list"):
            pods = list_items(pods_api.list(org_id=org_id, limit=200))
        else:
            pods = list_items(pods_api.list_by_organization(org_id, limit=200))
        pod_item = (
            select_from_items(pods, label="pod")
            if prompt
            else (pods[0] if pods else None)
        )
        if pod_item is None:
            fail(f"No pods found for organization {item_label(org_item, org_id)}.")

    remember_org(state, org_id)
    remember_pod(state, str(pod_item.get("id")))
    return {"selected_org": org_item, "selected_pod": pod_item}


def _run_init_flow(
    ctx: typer.Context,
    *,
    org: str | None = None,
    pod: str | None = None,
    prompt: bool = True,
) -> None:
    result = run_with_client(
        ctx,
        lambda client, state: _configure_defaults(
            client, state, org=org, pod=pod, prompt=prompt
        ),
    )
    if result is not None:
        emit(state_from_ctx(ctx), result)
