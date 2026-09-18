"""Sites the agent's browser is signed in to: see them, and sign one out.

The browser in your sandbox keeps its own profile on the durable disk, the
way the one on your desk does. So this is not a list of things Lemma has
stored on your behalf -- it is a read of what that browser is holding right
now, and `forget` really signs it out rather than deleting a copy.

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
    help="Sites the agent's browser is signed in to.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def browser_logins_root(ctx: typer.Context) -> None:
    """List them when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    list_logins(ctx)


@app.command("list")
def list_logins(
    ctx: typer.Context,
    wake: bool = typer.Option(
        False,
        "--wake",
        help="Start the computer if it is asleep. Off by default: reading the "
        "list means a round trip into the sandbox.",
    ),
) -> None:
    """Every site the browser is signed in to.

    Not paged, and it used to be. That machinery -- follow `next_page_token`
    to exhaustion, bounded so a permissive stub could not hang CI -- belonged
    to a table that grew. This is what one browser holds.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(ctx, lambda client, _s: client.web_logins.list(wake=wake))
    if result is not None:
        emit(state, result)


@app.command("forget")
def forget_login(
    ctx: typer.Context,
    origin: str = typer.Argument(
        ..., help="The site — app.example.com, or https://app.example.com"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Sign the agent's browser out of a site.

    Drops its cookies, so the next run meets the login wall and asks you.
    This is about the agent's browser only -- wherever you are signed in
    yourself is untouched.

    Needs the computer running. It refuses rather than reporting a success it
    did not achieve, which is the whole difference from the version of this
    that deleted a stored copy and left the browser signed in.
    """
    state = state_from_ctx(ctx)
    if not yes:
        typer.confirm(
            f"Sign the agent's browser out of {origin}? "
            "Your own sessions are not affected.",
            abort=True,
        )
    result = run_with_client(ctx, lambda client, _s: client.web_logins.remove(origin))
    if result is not None:
        emit(state, result)
