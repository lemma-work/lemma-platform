from __future__ import annotations

from pathlib import Path

import typer

from .context import selected_conversation, selected_org, selected_pod
from .io import emit
from .lazy import LazyEntry, LazyRootGroup
from .project_env import load_project_env
from .sdk import pod_client
from .state import build_state, run_with_client, state_from_ctx

# Command groups load lazily (see lazy.py): each entry maps the CLI name to
# (module, Typer-app attribute, one-line help, hidden). Insertion order is the
# listing order in `lemma --help`. The help strings are pinned to the real
# sub-app help by tests/test_lazy_groups.py.
_CMD = "lemma_cli.cli_core.commands"
LAZY_GROUPS: dict[str, LazyEntry] = {
    "auth": (f"{_CMD}.system", "auth_app", "Authentication commands.", False),
    "config": (f"{_CMD}.system", "config_app", "CLI context and per-server defaults (pod/org).", False),
    "servers": (f"{_CMD}.system", "server_app", "Show and manage Lemma CLI servers.", False),
    "agent-host": (
        "lemma_cli.agent_host.commands",
        "app",
        "Manage the durable local Agent Host.",
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
    "conversation": (f"{_CMD}.conversations", "app", "Agent conversation commands.", False),
    "conversations": (f"{_CMD}.conversations", "app", "Agent conversation commands.", False),
    "app": (f"{_CMD}.apps", "app", "App commands.", False),
    "apps": (f"{_CMD}.apps", "app", "App commands.", False),
    "schedule": (f"{_CMD}.schedules", "app", "Schedule commands.", False),
    "schedules": (f"{_CMD}.schedules", "app", "Schedule commands.", False),
    "file": (f"{_CMD}.files", "app", "Work with pod files like a normal filesystem: ls, cat, write, append, mkdir, upload, download, mv, rm, search.", False),
    "files": (f"{_CMD}.files", "app", "Work with pod files like a normal filesystem: ls, cat, write, append, mkdir, upload, download, mv, rm, search.", False),
    "table": (f"{_CMD}.data", "tables_app", "Table commands.", False),
    "tables": (f"{_CMD}.data", "tables_app", "Table commands.", False),
    "record": (f"{_CMD}.data", "records_app", "Record commands.", False),
    "records": (f"{_CMD}.data", "records_app", "Record commands.", False),
    "query": (f"{_CMD}.data", "query_app", "Query commands.", False),
    "datastore": (f"{_CMD}.data", "datastore_app", "Stream live datastore record changes.", False),
    "connector": (f"{_CMD}.connectors", "app", "Connector, account, and operation commands.", False),
    "connectors": (f"{_CMD}.connectors", "app", "Connector, account, and operation commands.", False),
    "surface": (f"{_CMD}.surfaces", "app", "Agent surface commands for Slack, Teams, Telegram, WhatsApp, Gmail, and Outlook.", False),
    "surfaces": (f"{_CMD}.surfaces", "app", "Agent surface commands for Slack, Teams, Telegram, WhatsApp, Gmail, and Outlook.", False),
    "profile": (f"{_CMD}.profile", "app", "View and edit the current user's Lemma profile.", False),
    "me": (f"{_CMD}.profile", "app", "View and edit the current user's Lemma profile.", False),
    "workflow": (f"{_CMD}.workflows", "app", "Workflow commands.", False),
    "workflows": (f"{_CMD}.workflows", "app", "Workflow commands.", False),
    "skill": (f"{_CMD}.skills", "app", "Install bundled Lemma agent skills into your coding agent (Claude Code, Codex, OpenCode, Cursor).", False),
    "skills": (f"{_CMD}.skills", "app", "Install bundled Lemma agent skills into your coding agent (Claude Code, Codex, OpenCode, Cursor).", False),
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
    import sys

    from .errors import report_cli_error

    try:
        app()
    except Exception as exc:  # noqa: BLE001 - re-raised unless we can do better
        if not report_cli_error(exc):
            raise
        # sys.exit, not typer.Exit: we are outside click's runtime here, so a
        # typer.Exit would itself escape as an unhandled exception.
        sys.exit(1)


@app.command("init")
def init(
    ctx: typer.Context,
    org: str | None = typer.Option(None, "--org"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
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


@app.command("feedback")
def feedback_cmd(
    ctx: typer.Context,
    category: str = typer.Option(..., "--category", help="cli, skill, platform, docs, …"),
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
        category=category,
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
    state = state_from_ctx(ctx)
    normalized = resource.lower().replace("_", "-")

    def run(client, s):  # type: ignore[no-untyped-def]
        pod_id = selected_pod(s, required=False)
        if normalized in {"org", "orgs", "organization", "organizations"}:
            return client.orgs.list(limit=limit) if name is None else client.orgs.get(name)
        if normalized in {"pod", "pods"}:
            if name is not None:
                return client.pods.get(name)
            return client.pods.list(org_id=selected_org(s), limit=limit)
        pod_sdk = pod_client(client, s, pod_id)
        if normalized in {"agent", "agents"}:
            return (
                pod_sdk.agents.get(name)
                if name
                else pod_sdk.agents.list(limit=limit)
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
            return (
                pod_sdk.apps.get(name)
                if name
                else pod_sdk.apps.list(limit=limit)
            )
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
                pod_sdk.tables.get(name)
                if name
                else pod_sdk.tables.list(limit=limit)
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


def _parse_chat_args(
    args: list[str], agent: str | None, message: str | None
) -> tuple[str | None, str | None]:
    if agent is not None:
        return agent, message if message is not None else (" ".join(args) or None)
    if not args:
        return None, message
    if len(args) == 1:
        return args[0], message
    return args[0], message if message is not None else " ".join(args[1:])


__all__ = ["app", "main", "LAZY_GROUPS"]


if __name__ == "__main__":
    main()
