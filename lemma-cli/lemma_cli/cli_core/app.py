from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from .context import selected_conversation, selected_org, selected_pod
from .io import emit
from .lazy import LazyEntry, LazyRootGroup
from .project_env import load_project_env
from .sdk import pod_client
from .state import build_state, err_console, run_with_client, state_from_ctx

# Command groups load lazily (see lazy.py): each entry maps the CLI name to
# (module, Typer-app attribute, one-line help, hidden). Insertion order is the
# listing order in `lemma --help`. The help strings are pinned to the real
# sub-app help by tests/test_lazy_groups.py.
_CMD = "lemma_cli.cli_core.commands"
LAZY_GROUPS: dict[str, LazyEntry] = {
    "auth": (f"{_CMD}.system", "auth_app", "Authentication commands.", False),
    "config": (
        f"{_CMD}.system",
        "config_app",
        "CLI context and per-server defaults (pod/org).",
        False,
    ),
    "servers": (
        f"{_CMD}.system",
        "server_app",
        "Show and manage Lemma CLI servers.",
        False,
    ),
    "telemetry": (
        f"{_CMD}.system",
        "telemetry_app",
        "Anonymous CLI usage telemetry.",
        False,
    ),
    "runtime": (f"{_CMD}.runtime", "app", "Agent runtime profiles.", False),
    "org": (f"{_CMD}.organizations", "app", "Organization commands.", False),
    "orgs": (f"{_CMD}.organizations", "app", "Organization commands.", False),
    "organization": (f"{_CMD}.organizations", "app", "Organization commands.", True),
    "organizations": (f"{_CMD}.organizations", "app", "Organization commands.", True),
    "pod": (f"{_CMD}.pods", "app", "Pod commands.", False),
    "pods": (f"{_CMD}.pods", "app", "Pod commands.", False),
    "agent": (f"{_CMD}.agents", "app", "Agent commands.", False),
    "agents": (f"{_CMD}.agents", "app", "Agent commands.", False),
    "function": (f"{_CMD}.functions", "app", "Function commands.", False),
    "functions": (f"{_CMD}.functions", "app", "Function commands.", False),
    "conversation": (
        f"{_CMD}.conversations",
        "app",
        "Agent conversation commands.",
        False,
    ),
    "conversations": (
        f"{_CMD}.conversations",
        "app",
        "Agent conversation commands.",
        False,
    ),
    "app": (f"{_CMD}.apps", "app", "App commands.", False),
    "apps": (f"{_CMD}.apps", "app", "App commands.", False),
    "schedule": (f"{_CMD}.schedules", "app", "Schedule commands.", False),
    "schedules": (f"{_CMD}.schedules", "app", "Schedule commands.", False),
    "file": (
        f"{_CMD}.files",
        "app",
        "Work with pod files like a normal filesystem: ls, cat, write, append, mkdir, upload, download, mv, rm, search.",
        False,
    ),
    "files": (
        f"{_CMD}.files",
        "app",
        "Work with pod files like a normal filesystem: ls, cat, write, append, mkdir, upload, download, mv, rm, search.",
        False,
    ),
    "table": (f"{_CMD}.data", "tables_app", "Table commands.", False),
    "tables": (f"{_CMD}.data", "tables_app", "Table commands.", False),
    "record": (f"{_CMD}.data", "records_app", "Record commands.", False),
    "records": (f"{_CMD}.data", "records_app", "Record commands.", False),
    "query": (f"{_CMD}.data", "query_app", "Query commands.", False),
    "datastore": (
        f"{_CMD}.data",
        "datastore_app",
        "Stream live datastore record changes.",
        False,
    ),
    "connector": (
        f"{_CMD}.connectors",
        "app",
        "Connector, account, and operation commands.",
        False,
    ),
    "connectors": (
        f"{_CMD}.connectors",
        "app",
        "Connector, account, and operation commands.",
        False,
    ),
    "surface": (
        f"{_CMD}.surfaces",
        "app",
        "Agent surface commands for Slack, Teams, Telegram, WhatsApp, Gmail, and Outlook.",
        False,
    ),
    "surfaces": (
        f"{_CMD}.surfaces",
        "app",
        "Agent surface commands for Slack, Teams, Telegram, WhatsApp, Gmail, and Outlook.",
        False,
    ),
    "profile": (
        f"{_CMD}.profile",
        "app",
        "View and edit the current user's Lemma profile.",
        False,
    ),
    "me": (
        f"{_CMD}.profile",
        "app",
        "View and edit the current user's Lemma profile.",
        False,
    ),
    "workflow": (f"{_CMD}.workflows", "app", "Workflow commands.", False),
    "workflows": (f"{_CMD}.workflows", "app", "Workflow commands.", False),
    "skill": (
        f"{_CMD}.skills",
        "app",
        "Install bundled Lemma agent skills into your coding agent (Claude Code, Codex, OpenCode, Cursor).",
        False,
    ),
    "skills": (
        f"{_CMD}.skills",
        "app",
        "Install bundled Lemma agent skills into your coding agent (Claude Code, Codex, OpenCode, Cursor).",
        False,
    ),
}
LazyRootGroup.registry = LAZY_GROUPS

app = typer.Typer(
    name="lemma",
    cls=LazyRootGroup,
    help="Lemma CLI for pods, agents, functions, schedules, apps, conversations, files, data, connectors, and surfaces.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if not value:
        return
    from .versions import version_lines

    for line in version_lines():
        typer.echo(line)
    raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show CLI, SDK, and API schema versions, then exit.",
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Backend API base URL."
    ),
    auth_url: str | None = typer.Option(None, "--auth-url", help="Auth frontend URL."),
    token: str | None = typer.Option(
        None, "--token", help="Bearer token. Falls back to env/config."
    ),
    server_name: str | None = typer.Option(
        None,
        "--server",
        help="Stored CLI server. Can also be set with LEMMA_SERVER.",
    ),
    org: str | None = typer.Option(
        None, "--org", help="Organization id/slug for this command."
    ),
    pod: str | None = typer.Option(None, "--pod", help="Pod id/slug for this command."),
    conversation: str | None = typer.Option(
        None, "--conversation-id", help="Conversation id for this command."
    ),
    timeout: float = typer.Option(60.0, "--timeout", min=1.0),
    config_file: Path = typer.Option(Path("~/.lemma/config.json"), "--config-file"),
    no_verify_ssl: bool = typer.Option(False, "--no-verify-ssl"),
    output: str = typer.Option("pretty", "--output", help="pretty or json."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print raw JSON output. Works anywhere, e.g. lemma pods list --json.",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Expand folded/truncated fields in pretty output (schemas, long values).",
    ),
) -> None:
    # Load the project-folder .lemma.env family BEFORE build_state so its
    # LEMMA_SERVER / LEMMA_POD_ID feed server selection and pod resolution. The
    # loader keys off the same server build_state will pick; real process env always
    # wins (setdefault semantics).
    project_env = load_project_env(
        server_flag=server_name, config_file=config_file.expanduser()
    )
    # The loader detects a committed secret and refuses to apply it; saying so is
    # the other half of the feature. Silence here is what turned a typo into an
    # unanswerable support question: the CLI switched servers and then told the
    # user to unset a variable they had never set.
    if project_env.get("token_in_committed_file"):
        err_console.print(
            f"[yellow]warning[/yellow] LEMMA_TOKEN in "
            f"{project_env['project_dir']}/{project_env['token_file']} was ignored — "
            "these files are committed, so a token there is a leaked secret. "
            "Move it to the gitignored .local variant, or run `lemma auth login`."
        )
    state = build_state(
        config_file=config_file.expanduser(),
        server=server_name,
        base_url=base_url,
        auth_url=auth_url,
        token=token,
        timeout=timeout,
        no_verify_ssl=no_verify_ssl,
        output="json" if json_output else output,
        full=full,
    )
    state.project_env = project_env
    state.config.setdefault("_runtime", {})
    state.config["_runtime"]["org"] = org
    state.config["_runtime"]["pod"] = pod
    state.config["_runtime"]["conversation"] = conversation
    ctx.obj = state


def main() -> None:
    """Entry point, and the CLI's last error boundary.

    Without this, anything the SDK raises that a command didn't catch escapes to
    Typer's rich traceback: ~360 lines of framework frames whose only payload is
    the last line. `LemmaConnectionError: [Errno 61] Connection refused` is the
    common case — an unreachable server is a normal, expected condition, not a
    crash, and an agent should not have to spend its context reading httpx
    internals to learn that `make dev` isn't running.
    """
    import os
    import sys

    from .errors import report_cli_error

    # Say what we are on every request. The SDK cannot tell a CLI invocation
    # apart from any other program using it, and "a person ran a command" is a
    # different fact from "something called the API".
    os.environ.setdefault("LEMMA_CLIENT", "lemma-cli")

    command = _invoked_command(sys.argv[1:])
    exit_status = "ok"
    try:
        app()
    except SystemExit as exc:
        exit_status = "ok" if not exc.code else "error"
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised unless we can do better
        exit_status = "error"
        if not report_cli_error(exc):
            raise
        # sys.exit, not typer.Exit: we are outside click's runtime here, so a
        # typer.Exit would itself escape as an unhandled exception.
        sys.exit(1)
    finally:
        from .telemetry import record_command
        from .update import maybe_check_in_background, notify_if_available

        record_command(command, exit_status=exit_status)
        # After the command, never before it: the check runs on a daemon thread
        # and only records what it saw, and the notice is printed from what a
        # previous invocation recorded. Nothing here waits on the network.
        maybe_check_in_background()
        notify_if_available()


#: Top-level commands registered directly on `app` rather than as lazy groups.
#: Anything missing here is reported as `None` by telemetry, which is how
#: `doctor`, `schema`, `feedback`, `get` and `describe` went unmeasured.
_TOP_LEVEL_COMMANDS = frozenset(
    {
        "chat",
        "describe",
        "doctor",
        "feedback",
        "get",
        "init",
        "schema",
        "update",
        "version",
    }
)


def _invoked_command(argv: list[str]) -> str | None:
    """The command group that was run, or nothing.

    Matched against the registered groups rather than read off the command
    line, so an unrecognised first token — which could be anything a user
    typed, including a path or a typo carrying a name — never becomes a
    telemetry dimension. Arguments and flag values are never looked at.
    """
    known = set(LAZY_GROUPS) | _TOP_LEVEL_COMMANDS
    for token in argv:
        if token.startswith("-"):
            continue
        return token if token in known else None
    return None


@app.command("init")
def init(
    ctx: typer.Context,
    org: str | None = typer.Option(None, "--org"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Select the default organization and pod for the active server."""
    from .commands import system

    system._run_init_flow(ctx, org=org, pod=pod, prompt=True)


@app.command("schema")
def schema_cmd(
    resource: str = typer.Argument(
        ..., help="pod, table, function, agent, workflow, schedule, or surface."
    ),
) -> None:
    """Print the JSONC scaffold/example for a resource type — the canonical
    'what fields exist' reference. Same shape `lemma <resource> init` writes
    (and the same as the per-resource `lemma <resource> schema`)."""
    from .commands._authoring import print_resource_schema

    print_resource_schema(resource)


@app.command("version")
def version_cmd(ctx: typer.Context) -> None:
    """Show CLI, SDK, and bundled API schema versions."""
    from .commands import system

    system.run_version(ctx)


class FeedbackCategory(str, Enum):
    """Which part of Lemma a feedback report is about.

    Mirrors the API's `FeedbackCategory`, spelled out rather than imported:
    this module is the startup path for every command including
    `lemma --help`, and LAZY_GROUPS exists so nothing pulls the SDK in before
    a command actually runs. tests/test_cli_v2.py pins these to the generated
    enum, so the two cannot drift apart again.

    Typing the flag with this enum is the fix for the original bug: the help
    advertised `cli, skill, platform, docs` while the wire accepted only
    SCREAMING_SNAKE names, so every advertised value failed deep in the SDK
    with `'cli' is not a valid FeedbackCategory` and no list of what would
    work. A wrong value is now a parse-time error that names the choices.
    """

    CLI = "cli"
    SKILL = "skill"
    PLATFORM = "platform"
    DOCS = "docs"
    OTHER = "other"


@app.command("feedback")
def feedback_cmd(
    ctx: typer.Context,
    category: FeedbackCategory = typer.Option(
        ..., "--category", help="Which part of Lemma the report is about."
    ),
    subject: str = typer.Option(..., "--subject", help="One-line summary."),
    issue_encountered: str = typer.Option(..., "--issue-encountered"),
    expected_behavior: str = typer.Option(..., "--expected-behavior"),
    actual_behavior: str = typer.Option(..., "--actual-behavior"),
    suggested_next_steps: str | None = typer.Option(None, "--suggested-next-steps"),
) -> None:
    """Report a problem with the CLI, a skill, or the platform."""
    from .commands import system

    system.run_feedback(
        ctx,
        category=category.value,
        subject=subject,
        issue_encountered=issue_encountered,
        expected_behavior=expected_behavior,
        actual_behavior=actual_behavior,
        suggested_next_steps=suggested_next_steps,
    )


@app.command("doctor")
def doctor_cmd(ctx: typer.Context) -> None:
    """Diagnose client/server version skew and duplicate installs."""
    from .commands import system

    system.run_doctor(ctx)


@app.command("update")
def update_cmd(
    ctx: typer.Context,
    version: str | None = typer.Option(
        None, "--version", help="Install this exact version instead of the newest."
    ),
) -> None:
    """Upgrade this lemma CLI in place."""
    from .commands import system

    system.run_update(ctx, version=version)


@app.command("get")
def get_resource(
    ctx: typer.Context,
    resource: str = typer.Argument(
        ...,
        help="pods, agents, functions, schedules, apps, files, tables, records, workflows, conversations",
    ),
    name: str | None = typer.Argument(None),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Fetch one resource, or list them when no name is given."""
    state = state_from_ctx(ctx)
    normalized = resource.lower().replace("_", "-")

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_id = selected_pod(s, required=False)
        if normalized in {"org", "orgs", "organization", "organizations"}:
            return (
                client.orgs.list(limit=limit) if name is None else client.orgs.get(name)
            )
        if normalized in {"pod", "pods"}:
            if name is not None:
                return client.pods.get(name)
            return client.pods.list(org_id=selected_org(s), limit=limit)
        pod_sdk = pod_client(client, s, pod_id)
        if normalized in {"agent", "agents"}:
            return (
                pod_sdk.agents.get(name) if name else pod_sdk.agents.list(limit=limit)
            )
        if normalized in {"function", "functions"}:
            return (
                pod_sdk.functions.get(name)
                if name
                else pod_sdk.functions.list(limit=limit)
            )
        if normalized in {"schedule", "schedules"}:
            return (
                pod_sdk.schedules.get(name)
                if name
                else pod_sdk.schedules.list(limit=limit)
            )
        if normalized in {"app", "apps"}:
            return pod_sdk.apps.get(name) if name else pod_sdk.apps.list(limit=limit)
        if normalized in {"workflow", "workflows"}:
            return (
                pod_sdk.workflows.get(name)
                if name
                else pod_sdk.workflows.list(limit=limit)
            )
        if normalized in {"conversation", "conversations"}:
            return (
                pod_sdk.conversations.get(name)
                if name
                else pod_sdk.conversations.list(limit=limit)
            )
        if normalized in {"table", "tables"}:
            return (
                pod_sdk.tables.get(name) if name else pod_sdk.tables.list(limit=limit)
            )
        if normalized in {"file", "files"}:
            path = name or "/me"
            from .paths import api_path

            return pod_sdk.files.list(api_path(path), limit=limit)
        raise typer.BadParameter(f"Unknown resource: {resource}")

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@app.command("describe")
def describe_resource(
    ctx: typer.Context,
    resource: str = typer.Argument(
        "pod",
        help="Resource to describe. Defaults to the selected pod inventory.",
    ),
    name: str | None = typer.Argument(None),
) -> None:
    """Summarise a resource — by default, the selected pod's inventory."""
    normalized = resource.lower().replace("_", "-")
    if normalized in {"pod", "pods"}:
        from .commands import pods

        pods.describe_pod(ctx, name, limit=50)
        return
    get_resource(ctx, resource, name, limit=100)


@app.command("chat")
def chat(
    ctx: typer.Context,
    args: list[str] | None = typer.Argument(
        None, metavar="[AGENT] [MESSAGE...]", help="Agent name and optional message."
    ),
    agent: str | None = typer.Option(
        None, "--agent", "-a", help="Agent name. Omit for the default pod agent."
    ),
    message: str | None = typer.Option(
        None, "--message", "-m", help="Send one message and exit."
    ),
    conversation: str | None = typer.Option(None, "--conversation"),
    title: str | None = typer.Option(None, "--title"),
) -> None:
    """Chat with a pod agent, interactively or with a single message."""
    from .commands import conversations

    state = state_from_ctx(ctx)
    selected = selected_conversation(state, conversation, required=False)
    parsed_agent, parsed_message = _parse_chat_args(args or [], agent, message)
    if parsed_message is None:
        conversations.interactive_chat(
            ctx,
            agent=parsed_agent,
            pod=None,
            conversation=selected,
            title=title,
        )
        return
    conversations.chat_once(
        ctx,
        agent=parsed_agent,
        message=parsed_message,
        pod=None,
        conversation=selected,
        title=title,
        show_header=True,
        show_user_message=True,
    )


def _looks_like_message(token: str) -> bool:
    """Whether a lone positional is a message rather than an agent name.

    Agent names are single words; anything the shell hands us as one argv entry
    containing whitespace was quoted prose. Without this,
    `lemma chat "what can you do in this pod?"` — the shape the README teaches —
    took the whole question as an agent name and opened an interactive session
    against an agent nobody has.
    """
    return any(character.isspace() for character in token.strip())


def _parse_chat_args(
    args: list[str], agent: str | None, message: str | None
) -> tuple[str | None, str | None]:
    if agent is not None:
        return agent, message if message is not None else (" ".join(args) or None)
    if not args:
        return None, message
    if len(args) == 1:
        if _looks_like_message(args[0]):
            # An explicit --message still wins, matching the multi-word branch.
            return None, message if message is not None else args[0]
        return args[0], message
    return args[0], message if message is not None else " ".join(args[1:])


__all__ = ["app", "main", "LAZY_GROUPS"]


if __name__ == "__main__":
    main()
