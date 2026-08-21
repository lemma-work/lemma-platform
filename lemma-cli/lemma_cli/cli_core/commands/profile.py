from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ..io import emit
from ..payload import read_json
from ..state import run_with_client, state_from_ctx

app = typer.Typer(help="View and edit the current user's Lemma profile.")


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


@app.command("get")
def get_profile(ctx: typer.Context) -> None:
    """Show your user profile."""
    state = state_from_ctx(ctx)
    result = run_with_client(ctx, lambda client, _state: client.user.profile())
    if result is not None:
        emit(state, result)


@app.command("update")
def update_profile(
    ctx: typer.Context,
    first_name: str | None = typer.Option(None, "--first-name"),
    last_name: str | None = typer.Option(None, "--last-name"),
    mobile_number: str | None = typer.Option(None, "--mobile-number", "--phone"),
    telegram_username: str | None = typer.Option(None, "--telegram-username"),
    country: str | None = typer.Option(None, "--country"),
    timezone: str | None = typer.Option(None, "--timezone"),
    date_of_birth: str | None = typer.Option(None, "--date-of-birth"),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload."
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
    """Update your user profile."""
    state = state_from_ctx(ctx)
    payload = read_json(json_payload, file, required=False)
    payload.update(
        _clean_payload(
            {
                "first_name": first_name,
                "last_name": last_name,
                "mobile_number": mobile_number,
                "telegram_username": telegram_username,
                "country": country,
                "timezone": timezone,
                "date_of_birth": date_of_birth,
            }
        )
    )
    result = run_with_client(
        ctx,
        lambda client, _state: client.user.update_profile(payload),
    )
    if result is not None:
        emit(state, result)
