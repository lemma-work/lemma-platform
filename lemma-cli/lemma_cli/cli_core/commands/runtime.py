from __future__ import annotations

from pathlib import Path

import typer

from ..io import emit
from ..payload import read_json
from ..state import run_with_client, state_from_ctx

app = typer.Typer(help="Manage agent runtime profiles.")
profiles_app = typer.Typer(help="Runtime profile commands for the selected organization.")
app.add_typer(profiles_app, name="profiles")

PROFILE_TYPES = (
    "OPENAI_COMPATIBLE",
    "ANTHROPIC_COMPATIBLE",
    "AZURE_OPENAI",
    "GOOGLE_VERTEX",
    "HARNESS",
)


@profiles_app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    """List runtime profiles visible in the selected organization."""
    state = state_from_ctx(ctx)
    result = run_with_client(ctx, lambda client, _s: client.org_runtime.profiles())
    if result is not None:
        emit(state, result)


@profiles_app.command("get")
def get_profile(
    ctx: typer.Context,
    profile_id: str = typer.Argument(..., help="Runtime profile id."),
) -> None:
    """Get one runtime profile."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.org_runtime.get_profile(profile_id),
    )
    if result is not None:
        emit(state, result)


@profiles_app.command("create")
def create_profile(
    ctx: typer.Context,
    runtime_type: str = typer.Argument(
        ...,
        help=", ".join(PROFILE_TYPES),
    ),
    name: str | None = typer.Option(None, "--name", help="Profile display name."),
    scope: str = typer.Option("PERSONAL", "--scope", help="PERSONAL or ORGANIZATION."),
    harness_id: str | None = typer.Option(
        None, "--harness-id", help="Harness UUID (HARNESS only)."
    ),
    harness_snapshot_revision: str | None = typer.Option(
        None,
        "--harness-revision",
        help="Current harness configuration revision.",
    ),
    base_url: str | None = typer.Option(None, "--base-url"),
    azure_endpoint: str | None = typer.Option(None, "--azure-endpoint"),
    api_version: str | None = typer.Option(None, "--api-version"),
    project_id: str | None = typer.Option(None, "--project-id"),
    location: str | None = typer.Option(None, "--location"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Provider API key (stored encrypted)."
    ),
    default_model_name: str | None = typer.Option(None, "--default-model"),
    model_names: list[str] | None = typer.Option(
        None, "--model", help="Provider catalog model. Repeat for multiple."
    ),
    description: str | None = typer.Option(None, "--description"),
    data: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Create a personal or organization runtime profile."""
    state = state_from_ctx(ctx)
    payload = read_json(data, file, required=False)
    payload["runtime_type"] = (
        payload.get("runtime_type") or runtime_type
    ).upper()
    payload["scope"] = (payload.get("scope") or scope).upper()
    for key, value in (
        ("name", name),
        ("harness_id", harness_id),
        ("harness_snapshot_revision", harness_snapshot_revision),
        ("base_url", base_url),
        ("azure_endpoint", azure_endpoint),
        ("api_version", api_version),
        ("project_id", project_id),
        ("location", location),
        ("api_key", api_key),
        ("default_model_name", default_model_name),
        ("description", description),
    ):
        if value is not None:
            payload[key] = value
    if model_names:
        payload["model_names"] = list(model_names)
    result = run_with_client(
        ctx,
        lambda client, _s: client.org_runtime.create_profile(payload),
    )
    if result is not None:
        emit(state, result)


@profiles_app.command("update")
def update_profile(
    ctx: typer.Context,
    profile_id: str = typer.Argument(...),
    data: str | None = typer.Option(None, "--data", "-d"),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Update mutable runtime profile settings."""
    state = state_from_ctx(ctx)
    payload = read_json(data, file, required=True)
    result = run_with_client(
        ctx,
        lambda client, _s: client.org_runtime.update_profile(profile_id, payload),
    )
    if result is not None:
        emit(state, result)


@profiles_app.command("refresh")
def refresh_profile(ctx: typer.Context, profile_id: str) -> None:
    """Refresh a provider catalog or validate a harness snapshot."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.org_runtime.refresh_profile(profile_id),
    )
    if result is not None:
        emit(state, result)


@profiles_app.command("delete")
def delete_profile(ctx: typer.Context, profile_id: str) -> None:
    """Disable a runtime profile."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.org_runtime.delete_profile(profile_id),
    )
    if result is not None:
        emit(state, result)
