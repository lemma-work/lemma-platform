from __future__ import annotations

from pathlib import Path

import typer

from ..io import emit, list_items
from ..payload import read_json
from ..state import fail, run_with_client, state_from_ctx

app = typer.Typer(help="Agent runtime profiles.")
profiles_app = typer.Typer(help="Organization runtime profile commands.")
app.add_typer(profiles_app, name="profiles")

PROFILE_SOURCES = ("AGENT_HOST", "OPENAI_COMPATIBLE", "ANTHROPIC_COMPATIBLE")


@profiles_app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    """List runtime profiles (system + organization) for the selected org."""
    state = state_from_ctx(ctx)
    result = run_with_client(ctx, lambda client, _s: client.org_runtime.profiles())
    if result is not None:
        emit(state, result)


@profiles_app.command("get")
def get_profile(
    ctx: typer.Context,
    selector: str = typer.Argument(
        ..., help="Profile id or name (case-insensitive)."
    ),
) -> None:
    """Show one runtime profile, including its model catalog.

    There is no server-side get-by-id endpoint, so this filters the profile list
    client-side — a CLI/auth-only way to read a profile's models (e.g. to populate
    an app's model picker) without hitting the API directly."""
    state = state_from_ctx(ctx)

    def _run(client, _s):  # type: ignore[no-untyped-def]
        profiles = list_items(client.org_runtime.profiles())
        needle = selector.casefold()
        for profile in profiles:
            if (
                str(profile.get("id") or "") == selector
                or str(profile.get("name") or "").casefold() == needle
            ):
                return profile
        names = ", ".join(
            sorted(str(p.get("name")) for p in profiles if p.get("name"))
        ) or "(none)"
        fail(f"Runtime profile not found: '{selector}'. Available: {names}.")

    result = run_with_client(ctx, _run)
    if result is not None:
        emit(state, result)


@profiles_app.command("create")
def create_profile(
    ctx: typer.Context,
    source: str = typer.Argument(
        ..., help="AGENT_HOST, OPENAI_COMPATIBLE, or ANTHROPIC_COMPATIBLE."
    ),
    name: str | None = typer.Option(None, "--name", help="Profile display name."),
    harness_id: str | None = typer.Option(
        None,
        "--harness-id",
        help=(
            "Agent Host harness UUID (AGENT_HOST only). "
            "Lemma Desktop lists them under Models."
        ),
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Provider base URL (OPENAI_COMPATIBLE)."
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Provider API key (stored encrypted, never returned)."
    ),
    default_model_name: str | None = typer.Option(None, "--default-model"),
    model_names: list[str] | None = typer.Option(
        None, "--model", help="Catalog model name. Repeat for multiple."
    ),
    description: str | None = typer.Option(None, "--description"),
    data: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Create an organization runtime profile."""
    state = state_from_ctx(ctx)
    payload = read_json(data, file, required=False)
    payload["source"] = (payload.get("source") or source).upper()
    for key, value in (
        ("name", name),
        ("harness_id", harness_id),
        ("base_url", base_url),
        ("api_key", api_key),
        ("default_model_name", default_model_name),
        ("description", description),
    ):
        if value is not None:
            payload[key] = value
    if model_names:
        payload["model_names"] = list(model_names)
    result = run_with_client(
        ctx, lambda client, _s: client.org_runtime.create_profile(payload)
    )
    if result is not None:
        emit(state, result)
