from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

import typer
from rich.console import Console

from lemma_sdk.auth import refresh_cli_session
from lemma_sdk.errors import LemmaAPIError

if TYPE_CHECKING:
    # The Lemma client pulls in the full resource/model tree; import it only when
    # a command actually opens a session (see client_session), not at CLI startup.
    from lemma_sdk import Lemma
from lemma_sdk.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SERVER_NAME,
    clear_auth_session,
    config_lock,
    build_env_server_config,
    ENV_SERVER_NAME,
    get_access_token_from_config,
    get_server_config,
    get_refresh_token_from_config,
    load_config,
    normalize_server_config,
    put_server_config,
    resolve_auth_url,
    resolve_base_url,
    resolve_token,
    resolve_verify_ssl,
    save_config,
    should_use_env_server,
    upsert_auth_session,
)

console = Console()


@dataclass
class CliState:
    config_path: Path
    config: dict[str, Any]
    base_url: str | None
    auth_url: str | None
    token: str | None
    timeout: float
    no_verify_ssl: bool
    output: str
    full: bool = False
    root_config: dict[str, Any] | None = None
    server: str = DEFAULT_SERVER_NAME
    server_source: str = "config"
    server_read_only: bool = False
    # Summary of a loaded project .lemma.env (path/local_path/applied keys), or
    # None when no project file was found. Surfaced by `config show`.
    project_env: dict[str, Any] | None = None


def build_state(
    *,
    config_file: Path = DEFAULT_CONFIG_PATH,
    server: str | None = None,
    base_url: str | None,
    auth_url: str | None,
    token: str | None,
    timeout: float,
    no_verify_ssl: bool,
    output: str,
    full: bool = False,
) -> CliState:
    requested_server = server or os.getenv("LEMMA_SERVER")
    root_config, selected_server = normalize_server_config(
        load_config(config_file),
        selected_server=requested_server,
    )
    env_server = should_use_env_server(requested_server)
    server_config = (
        build_env_server_config()
        if env_server
        else get_server_config(root_config, selected_server)
    )
    return CliState(
        config_path=config_file,
        config=server_config,
        root_config=root_config,
        server=ENV_SERVER_NAME if env_server else selected_server,
        server_source="env" if env_server else "config",
        server_read_only=env_server,
        base_url=base_url,
        auth_url=auth_url,
        token=token,
        timeout=timeout,
        no_verify_ssl=no_verify_ssl,
        output=output,
        full=full,
    )


def state_from_ctx(ctx: typer.Context) -> CliState:
    state = ctx.obj
    if not isinstance(state, CliState):
        raise typer.Exit(code=1)
    return state


def fail(message: str, *, code: int = 1) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=code)


def _extract_field_errors(details: Any) -> list[str]:
    """Pull field-level messages out of an API validation payload (FastAPI 422
    shape: a list of {loc, msg}, possibly wrapped in {"detail": [...]})."""
    items = details
    if isinstance(details, dict):
        items = details.get("detail") or details.get("errors") or []
    out: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                loc = [str(p) for p in (item.get("loc") or []) if p != "body"]
                name = ".".join(loc) if loc else str(item.get("field") or "")
                msg = str(item.get("msg") or item.get("message") or "").strip()
                out.append(f"{name}: {msg}".strip(": ").strip() if name else msg)
            elif isinstance(item, str):
                out.append(item)
    return [line for line in out if line]


def humanize_error(exc: Exception) -> str:
    """Turn raw create/import failures into actionable messages: a bare
    `KeyError` from `*Request.from_dict` becomes 'Missing required field: X',
    and API validation details are appended to the API error line."""
    if isinstance(exc, KeyError):
        key = exc.args[0] if exc.args else ""
        return f"Missing required field: {key}." if key else "Missing required field."
    if isinstance(exc, LemmaAPIError):
        if getattr(exc, "status_code", None) == 429:
            return (
                str(exc)
                + "\nYou've hit a rate limit — export/import are capped per day. "
                "Wait and try again, or ask an admin to raise the limit."
            )
        fields = _extract_field_errors(exc.details)
        if fields:
            return str(exc) + "\n" + "\n".join(f"  - {line}" for line in fields)
        return str(exc)
    return str(exc)


@contextmanager
def client_session(state: CliState) -> Iterator[Lemma]:
    from lemma_sdk import Lemma

    use_env = state.server_source == "env"
    defaults = state.config.get("defaults") if isinstance(state.config.get("defaults"), dict) else {}
    runtime = state.config.get("_runtime") if isinstance(state.config.get("_runtime"), dict) else {}
    with Lemma(
        base_url=resolve_base_url(state.base_url, state.config, use_env=use_env),
        token=resolve_token(state.token, state.config, use_env=use_env),
        org_id=runtime.get("org") or defaults.get("org_id"),
        pod_id=runtime.get("pod") or defaults.get("pod_id"),
        timeout=state.timeout,
        verify_ssl=resolve_verify_ssl(state.no_verify_ssl),
    ) as client:
        yield client


def refresh_and_retry(state: CliState, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except LemmaAPIError as exc:
        can_refresh = (
            exc.status_code == 401
            and state.server_source != "env"
            and not state.token
            and bool(get_refresh_token_from_config(state.config))
        )
        if not can_refresh:
            raise

        refresh_auth_session(state)
        return fn()


def refresh_auth_session(state: CliState) -> bool:
    if state.server_source == "env" or state.token:
        return False
    original_access_token = get_access_token_from_config(state.config)
    original_refresh_token = get_refresh_token_from_config(state.config)
    if not original_refresh_token:
        return False

    with config_lock(state.config_path):
        latest_root_raw = load_config(state.config_path)
        if state.root_config is not None:
            latest_root, _ = normalize_server_config(
                latest_root_raw,
                selected_server=state.server,
            )
            latest_config = get_server_config(latest_root, state.server)
        else:
            latest_root = None
            latest_config = latest_root_raw
        latest_access_token = get_access_token_from_config(latest_config)
        latest_refresh_token = get_refresh_token_from_config(latest_config)
        if (
            latest_refresh_token
            and latest_refresh_token != original_refresh_token
            and latest_access_token
            and latest_access_token != original_access_token
        ):
            state.config = latest_config
            if latest_root is not None:
                state.root_config = latest_root
            return True

        refreshed = refresh_cli_session(
            base_url=resolve_base_url(
                state.base_url,
                latest_config,
                use_env=state.server_source == "env",
            ),
            refresh_token=latest_refresh_token or original_refresh_token,
            verify_ssl=resolve_verify_ssl(state.no_verify_ssl),
            timeout=state.timeout,
        )
        state.config = upsert_auth_session(latest_config, refreshed)
        if latest_root is not None:
            state.root_config = put_server_config(
                latest_root, state.server, state.config
            )
            save_config(state.config_path, state.root_config)
        else:
            save_config(state.config_path, state.config)
    return True


def run_with_client(
    ctx: typer.Context, fn: Callable[[Lemma, CliState], Any]
) -> Any:
    state = state_from_ctx(ctx)
    # Imported here, not at module top: httpx costs ~80ms and every command
    # module imports this one, so an eager import lands on `lemma --help`.
    from httpx import HTTPError

    def invoke() -> Any:
        with client_session(state) as client:
            return fn(client, state)

    try:
        return refresh_and_retry(state, invoke)
    except (LemmaAPIError, HTTPError, OSError, ValueError) as exc:
        # KeyError/TypeError are deliberately NOT caught here: request payloads
        # are built via payload.build_request, which converts a missing/mistyped
        # field into a ValueError. A bare KeyError/TypeError therefore signals a
        # real bug and should surface as a traceback, not a misleading message.
        fail(humanize_error(exc))
    return None


def update_config(
    state: CliState, mutator: Callable[[dict[str, Any]], None]
) -> dict[str, Any]:
    if state.server_read_only:
        fail(
            "The env server is read-only. Change LEMMA_* environment variables or select a stored server with --server."
        )
    mutator(state.config)
    if state.root_config is not None:
        state.root_config = put_server_config(
            state.root_config, state.server, state.config
        )
        save_config(state.config_path, state.root_config)
    else:
        save_config(state.config_path, state.config)
    return state.config


def clear_auth(state: CliState) -> None:
    if state.server_read_only:
        fail(
            "The env server is read-only. Unset LEMMA_TOKEN or select a stored server with --server."
        )
    state.config = clear_auth_session(state.config)
    if state.root_config is not None:
        state.root_config = put_server_config(
            state.root_config, state.server, state.config
        )
        save_config(state.config_path, state.root_config)
    else:
        save_config(state.config_path, state.config)


def resolved_auth_urls(state: CliState) -> tuple[str, str]:
    return (
        resolve_base_url(
            state.base_url, state.config, use_env=state.server_source == "env"
        ),
        resolve_auth_url(
            state.auth_url, state.config, use_env=state.server_source == "env"
        ),
    )
