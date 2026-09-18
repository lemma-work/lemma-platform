"""Site logins saved from the agent's browser: see them, and remove one.

A saved login is a session captured from the agent's browser after somebody
signed in, encrypted and scoped to that one site. The web app shows them on
the pod's Connectors page; this is the same three calls from a terminal, for
scripting and for when the thing that is broken is the UI.

There is deliberately no `add`. A login is created by signing in to the site
yourself, in the agent's browser, through `browser_sign_in` -- the whole point
is that no password passes through Lemma, so there is nothing here to type one
into.
"""

from __future__ import annotations

import typer

from ..io import emit
from ..state import run_with_client, state_from_ctx

app = typer.Typer(
    help="Site logins saved from the agent's browser.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def browser_logins_root(ctx: typer.Context) -> None:
    """List saved logins when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    list_logins(ctx)


#: A ceiling on how many pages one listing will follow. The server caps the
#: set well below this, so reaching it means the token is not advancing and
#: the honest thing is to stop with what we have rather than spin.
_MAX_PAGES = 50


def _all_pages(fetch):  # type: ignore[no-untyped-def]
    """Follow `next_page_token` to exhaustion, and no further.

    Paged to the end rather than one page at a time, for the same reason
    `files shares` is: a login you cannot see is a login you cannot revoke,
    and a caller who forgets to follow the token is left believing the first
    page is all of it.

    Both guards are load-bearing. The token must be a non-empty *string*,
    because `test_json_output_contract` drives every command against a
    permissive stub whose every attribute answers truthily -- an unbounded
    `while` there does not fail, it hangs, and it took a CI job down for its
    full thirty-minute budget with no output at all. The page ceiling catches
    the other shape of the same bug: a server that keeps handing back a token
    that does not advance.
    """
    items = []
    page_token = None
    for _ in range(_MAX_PAGES):
        page = fetch(page_token)
        batch = list(getattr(page, "items", None) or [])
        items.extend(batch)
        page_token = getattr(page, "next_page_token", None)
        if not batch or not isinstance(page_token, str) or not page_token:
            break
    return items


@app.command("list")
def list_logins(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", help="Page size, not a ceiling."),
) -> None:
    """Every site you have a saved login for."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: _all_pages(
            lambda token: client.web_logins.list(limit=limit, page_token=token)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("forget")
def forget_login(
    ctx: typer.Context,
    origin: str = typer.Argument(
        ..., help="The site, as scheme and host — https://app.example.com"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Remove Lemma's copy of a saved login.

    This does not sign you out of the site: what it deletes is the session
    Lemma kept, so the next run asks you to sign in again instead of restoring
    one that may no longer work.
    """
    state = state_from_ctx(ctx)
    if not yes:
        typer.confirm(
            f"Forget the saved login for {origin}? "
            "You stay signed in at the site itself.",
            abort=True,
        )
    result = run_with_client(ctx, lambda client, _s: client.web_logins.remove(origin))
    if result is not None:
        emit(state, result)


@app.command("history")
def login_history(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """What has happened to your saved logins: captures, injections, removals."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: _all_pages(
            lambda token: client.web_logins.history(limit=limit, page_token=token)
        ),
    )
    if result is not None:
        emit(state, result)
