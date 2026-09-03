from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ...cli_app.enums import SURFACE_PLATFORMS
from ..confirm import confirm_destructive
from ..io import emit
from ..payload import read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

# Single source for the platform help shown on every platform argument, and for
# the group's own help — spelling the list twice is how it came to advertise
# Gmail and Outlook long after they stopped being surfaces and became connectors.
_PLATFORM_HELP = ", ".join(SURFACE_PLATFORMS) + "."

app = typer.Typer(help=f"Agent surface commands for {_PLATFORM_HELP}")


@app.command("init")
def init_surface(
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    root: Path | None = typer.Option(
        None, "--root", help="Bundle root (default: enclosing pod.json or cwd)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Scaffold a surface bundle file for a platform. Set the agent + account, then import."""
    from ...cli_app.scaffold import ScaffoldError, init_resource, report

    try:
        result = init_resource(
            "surface", platform, root=root, force=force, platform=platform
        )
    except ScaffoldError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report(
        result,
        next_hint="set default_agent_name + account_id, then `lemma pods import .`",
    )


@app.command("schema")
def schema_surface() -> None:
    """Print the JSONC example/shape for a surface bundle file."""
    from ._authoring import print_resource_schema

    print_resource_schema("surface")


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _surface_payload(
    base: dict[str, Any],
    *,
    credential_mode: str | None = None,
    default_agent_name: str | None = None,
    account_id: str | None = None,
    allowed_domains: list[str] | None = None,
    allowed_email_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Build a SurfaceUpsertRequest body. Platform rides in the path, not here."""
    payload = dict(base)
    if credential_mode is not None:
        payload["credential_mode"] = credential_mode.upper()
    payload.update(
        _clean_payload(
            {
                "default_agent_name": default_agent_name,
                "account_id": account_id,
            }
        )
    )

    config = dict(payload.get("config") or {})
    identity = dict(config.get("identity") or {})
    if allowed_domains:
        identity["allowed_domains"] = [value.lower() for value in allowed_domains]
    if allowed_email_addresses:
        identity["allowed_email_addresses"] = [
            value.lower() for value in allowed_email_addresses
        ]
    if identity:
        config["identity"] = identity
    if config:
        payload["config"] = config
    return payload


@app.command("list")
def list_surfaces(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List agent surfaces in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_surface(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a surface by platform name."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.get(platform),
    )
    if result is not None:
        emit(state, result)


@app.command("upsert")
def upsert_surface(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
    default_agent_name: str | None = typer.Option(
        None, "--agent", "--agent-name", help="Default agent that handles messages."
    ),
    account_id: str | None = typer.Option(None, "--account", "--account-id"),
    credential_mode: str | None = typer.Option(
        None, "--credential-mode", help="SYSTEM or CUSTOM."
    ),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
    allowed_domains: list[str] | None = typer.Option(None, "--allowed-domain"),
    allowed_email_addresses: list[str] | None = typer.Option(None, "--allowed-email"),
    data: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Create or update the surface for a platform (one surface per platform per pod).

    A surface is unique per pod+platform, so this single command covers create,
    config/agent/account edits, and enable/disable. Only the fields you pass are
    applied to an existing surface.
    """
    state = state_from_ctx(ctx)
    payload = _surface_payload(
        read_json(data, file, required=False),
        credential_mode=credential_mode,
        default_agent_name=default_agent_name,
        account_id=account_id,
        allowed_domains=allowed_domains,
        allowed_email_addresses=allowed_email_addresses,
    )
    if enabled is not None:
        payload["is_enabled"] = enabled
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.upsert(
            platform.upper(), payload
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("channels")
def update_channels(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
    channel_id: str | None = typer.Option(None, "--channel-id"),
    channel_name: str | None = typer.Option(None, "--channel-name"),
    data: str | None = typer.Option(
        None,
        "--data",
        "-d",
        help='Raw JSON channel list, e.g. [{"channel_id": ..., "channel_name": ...}].',
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Replace the channels a surface answers in (Slack/Teams only).

    An allow-list, not a routing table: a surface answers as exactly one agent,
    so a channel says where that agent may be spoken to. Give an agent its own
    app if it needs its own channels.
    """
    state = state_from_ctx(ctx)
    raw = read_json(data, file, required=False)
    if isinstance(raw, dict) and "channels" in raw:
        channels = raw["channels"]
    elif isinstance(raw, list):
        channels = raw
    else:
        channels = [
            _clean_payload(
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                }
            )
        ]
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.upsert(
            platform.upper(), {"config": {"channels": channels}}
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("available-channels")
def available_channels(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List the channels/groups this surface can be routed to (Slack/Teams)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.channels(platform),
    )
    if result is not None:
        emit(state, result)


@app.command("enable")
def enable_surface(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Enable a surface (temporarily off vs. delete, which removes it entirely)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.upsert(
            platform.upper(), {"is_enabled": True}
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("disable")
def disable_surface(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Disable a surface without deleting it (keeps config + frees nothing)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.upsert(
            platform.upper(), {"is_enabled": False}
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("delete")
def delete_surface(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a surface entirely, freeing its account for use in another pod."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete {platform} surface?", yes)
    run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.delete(platform),
    )
    emit(state, {"deleted": platform})


@app.command("setup")
def setup_status(
    ctx: typer.Context,
    platform: str = typer.Argument(..., help=_PLATFORM_HELP),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show setup status, webhook info, admin consent, and the platform checklist."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.setup(platform),
    )
    if result is not None:
        emit(state, result)


@app.command("telegram-setup")
def start_telegram_setup(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    name: str | None = typer.Option(
        None, "--name", help="Pod-unique surface name. Defaults to telegram."
    ),
    default_agent_name: str | None = typer.Option(
        None,
        "--agent",
        "--agent-name",
        help="Agent that answers. Omit to answer as the pod assistant.",
    ),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
    data: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Start a managed Telegram bot setup and print the link that creates the bot.

    The bot is made inside Telegram: open the returned ``launch_url`` (or show it
    as a QR), name the bot there, and it binds to this pod on its own -- there is
    no token to copy back. The surface does not exist until that finishes, so
    poll ``telegram-setup-status SETUP_ID`` until ``bot_username`` is set.

    Omitting ``--agent`` answers as the pod assistant, which is what lets a brand
    new pod take Telegram messages before any agent has been created.
    """
    state = state_from_ctx(ctx)
    payload: dict[str, Any] = read_json(data, file, required=False) or {}
    if name is not None:
        payload["name"] = name
    if default_agent_name is not None:
        payload["default_agent_name"] = default_agent_name
    if enabled is not None:
        payload["is_enabled"] = enabled
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.start_telegram_bot_setup(
            payload
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("telegram-setup-status")
def telegram_setup_status(
    ctx: typer.Context,
    setup_id: str = typer.Argument(..., help="Setup id from `telegram-setup`."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show where a managed Telegram bot setup has got to.

    ``status`` moves while the person is in Telegram; ``bot_username`` and
    ``surface_id`` are only set once the bot exists and is bound to this pod.
    ``error`` is set when the setup failed rather than merely being unfinished.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).surfaces.get_telegram_bot_setup(
            setup_id
        ),
    )
    if result is not None:
        emit(state, result)
