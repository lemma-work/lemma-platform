"""lemma-stack: install and manage a fully-local Lemma stack."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from lemma_stack import __version__, orchestrate
from lemma_stack.config import render, store
from lemma_stack.context import AdminContext
from lemma_stack.output import (
    AdminError,
    confirm,
    console,
    fail,
    info,
    ok,
    print_json,
    warn,
)
from lemma_stack.paths import LocalPaths, enrich_path
from lemma_stack.register import install_lemma_cli, register_local_server
from lemma_stack.release import manifest as release_manifest
from lemma_stack.runtime import detect
from lemma_stack.stack import images, lifecycle
from lemma_stack.stack.specs import CONTAINER_PREFIX
from lemma_stack.supervise import run_supervisor

app = typer.Typer(
    name="lemma-stack",
    help="Install and manage a local Lemma stack rooted at ~/.lemma/local.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="Read and edit the stack configuration.", no_args_is_help=True)
db_app = typer.Typer(help="Postgres passthrough (infra has no host ports).", no_args_is_help=True)
redis_app = typer.Typer(help="Redis passthrough.", no_args_is_help=True)
self_app = typer.Typer(help="Information about lemma-stack itself.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(redis_app, name="redis")
app.add_typer(self_app, name="self")


def _load_context() -> AdminContext:
    return AdminContext.load()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _print_next_steps(config) -> None:
    """Post-install setup guidance: configure the backend env, then restart.

    The LLM model key is required — agents won't run without one. The Composio
    key is strongly recommended: it powers the app connectors / integrations.
    Both are UPPER_SNAKE env vars that route to [backend.env] in config.toml.
    """
    overrides = store.env_overrides(config, "backend")
    has_llm = bool(
        overrides.get("LEMMA_ANTHROPIC_API_KEY") or overrides.get("LEMMA_OPENAI_API_KEY")
    )
    has_composio = bool(overrides.get("COMPOSIO_API_KEY"))
    if has_llm and has_composio:
        return
    console.print("\n[bold]Finish setup — configure the backend env, then restart:[/bold]\n")
    step = 1
    if not has_llm:
        console.print(
            f"  {step}. LLM model key  [red](required — agents won't run without it)[/red]"
        )
        console.print("       lemma-stack config set LEMMA_ANTHROPIC_API_KEY sk-ant-...")
        console.print(
            "       [dim]# or an OpenAI-compatible key "
            "(base URL defaults to Fireworks; override with LEMMA_OPENAI_BASE_URL):[/dim]"
        )
        console.print("       lemma-stack config set LEMMA_OPENAI_API_KEY <key>")
        step += 1
    if not has_composio:
        console.print(
            f"  {step}. Composio key   "
            "[yellow](recommended — enables app connectors / integrations)[/yellow]"
        )
        console.print("       lemma-stack config set COMPOSIO_API_KEY <key>")
        step += 1
    console.print(f"  {step}. Apply changes: [bold]lemma-stack restart[/bold]")
    console.print(
        "\n  [dim]Stored under \\[backend.env] in ~/.lemma/local/config.toml "
        "(edit directly with `lemma-stack config edit`).[/dim]"
    )


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------


@app.command()
def install(
    runtime_choice: str = typer.Option(
        "auto", "--runtime", help="Container runtime: auto, docker, or podman."
    ),
    channel: Optional[str] = typer.Option(None, help="Release channel or version (default: stable)."),
    manifest_path: Optional[Path] = typer.Option(
        None, "--manifest", help="Install from a local release manifest JSON (testing/air-gap)."
    ),
    set_values: list[str] = typer.Option(
        [], "--set", help="Config values to apply (KEY=VALUE), e.g. LEMMA_ANTHROPIC_API_KEY=…"
    ),
    start_stack: bool = typer.Option(True, "--start/--no-start", help="Start after installing."),
    use_cli: bool = typer.Option(
        True,
        "--use-cli/--no-cli",
        help="Install the lemma CLI and register this stack as its active 'local' server.",
    ),
    assume_yes: bool = typer.Option(False, "-y", "--yes", help="Answer yes to prompts."),
) -> None:
    """Install the Lemma stack: pick a runtime, pull a release, start everything."""
    paths = LocalPaths()
    paths.ensure()
    config = store.load_or_create(paths)

    # 1. container runtime
    provider = detect.select_runtime(runtime_choice, assume_yes=assume_yes)
    config["runtime"]["provider"] = provider
    if channel:
        config["install"]["channel"] = channel

    # 2. user-supplied config values
    for pair in set_values:
        if "=" not in pair:
            raise AdminError(f"--set expects KEY=VALUE, got {pair!r}")
        key, _, value = pair.partition("=")
        store.set_value(config, key.strip(), value)

    store.save(paths, config)

    # 4. port availability (only host-published ports can collide)
    for name in ("frontend", "backend", "agentbox"):
        port = store.port(config, name)
        if _port_in_use(port):
            warn(
                f"port {port} ({name}) is already in use; "
                f"set ports.{name} in {paths.config_file} or stop the conflicting service"
            )

    # 5. release manifest
    manifest = orchestrate.resolve_manifest(
        config, paths, manifest_path=manifest_path, channel=channel
    )
    info(f"installing Lemma {manifest.version} via {provider}")

    # 6. pull images, start everything, register the CLI server
    if start_stack:
        orchestrate.bring_up(
            paths, config, provider=provider, manifest=manifest, do_register=use_cli
        )
    else:
        runtime = detect.ensure_ready(provider)
        images.pull_release(runtime, manifest, kreuzberg=store.feature(config, "kreuzberg"))
        release_manifest.pin(paths, manifest)

    # 7. install the lemma CLI and point it at this stack (server "local")
    if use_cli:
        install_lemma_cli()
        if not start_stack:
            register_local_server(
                base_url=render.backend_origin(config),
                auth_url=f"{render.frontend_origin(config)}/auth",
                make_active=True,
            )

    ok(f"Lemma {manifest.version} installed")
    if start_stack:
        info(f"  app:      {render.frontend_origin(config)}")
        info(f"  api:      {render.backend_origin(config)}")
        info(f"  api docs: {render.backend_origin(config)}/scalar")
        info(
            "  [dim]open the 127-0-0-1.sslip.io host above (it resolves to 127.0.0.1); "
            "sign-in is scoped to it, so localhost / 127.0.0.1 won't authenticate[/dim]"
        )

    _print_next_steps(config)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


@app.command()
def start() -> None:
    """Start (or reconcile) the installed stack."""
    ctx = _load_context()
    lifecycle.up(ctx.runtime, ctx.specs(), ctx.manifest, migrate=False)
    info(f"app: {render.frontend_origin(ctx.config)}")


@app.command()
def stop(
    infra: bool = typer.Option(False, "--infra", help="Also stop db/redis/supertokens/kreuzberg."),
) -> None:
    """Stop the stack (app services; --infra stops everything)."""
    ctx = _load_context()
    specs = ctx.specs()
    if not infra:
        specs = [s for s in specs if s.name in {"agentbox", "backend", "frontend"}]
    lifecycle.down(ctx.runtime, specs)


@app.command()
def restart() -> None:
    """Restart the stack, re-rendering config (apply config.toml changes)."""
    ctx = _load_context()
    specs = ctx.specs()
    lifecycle.down(ctx.runtime, specs)
    lifecycle.up(ctx.runtime, specs, ctx.manifest, migrate=False)


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show the state of every stack container."""
    ctx = _load_context()
    rows = lifecycle.status(ctx.runtime, ctx.specs())
    payload = {
        "version": ctx.manifest.version,
        "provider": ctx.provider,
        "root": str(ctx.paths.root),
        "services": rows,
    }
    if json_output:
        print_json(payload)
        return
    info(f"Lemma {ctx.manifest.version} ({ctx.provider}) — {ctx.paths.root}")
    table = Table()
    for column in ("service", "status", "health", "ports"):
        table.add_column(column)
    for row in rows:
        health = row["health"] or "-"
        state = f"[green]{row['status']}[/green]" if row["running"] else f"[red]{row['status']}[/red]"
        table.add_row(row["service"], state, health, ", ".join(row["ports"]) or "-")
    console.print(table)


@app.command()
def supervise(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Walk the startup phases without executing anything (UI dev)."
    ),
) -> None:
    """Desktop supervisor: JSON-line events on stdout, commands on stdin."""
    raise typer.Exit(run_supervisor(dry_run=dry_run))


@app.command()
def logs(
    service: str = typer.Argument(..., help="One of: db, redis, supertokens, kreuzberg, agentbox, backend, frontend."),
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(200, "--lines"),
) -> None:
    """Tail logs of one stack service."""
    ctx = _load_context()
    args = ["logs", "--tail", str(lines)]
    if follow:
        args.append("-f")
    raise typer.Exit(ctx.runtime.stream(*args, f"{CONTAINER_PREFIX}-{service}"))


@app.command()
def uninstall(
    purge_data: bool = typer.Option(
        False, "--purge-data", help="Also delete ~/.lemma/local data and the postgres volume."
    ),
    assume_yes: bool = typer.Option(False, "-y", "--yes"),
) -> None:
    """Remove all stack containers (and optionally all data)."""
    ctx = _load_context()
    if not confirm(
        "Remove all Lemma stack containers?" + (" AND ALL DATA?" if purge_data else ""),
        default=False,
        assume_yes=assume_yes,
    ):
        raise typer.Exit(1)
    specs = ctx.specs()
    lifecycle.down(ctx.runtime, specs, remove=True)
    ctx.runtime.run("network", "rm", render.NETWORK_NAME, check=False)
    if purge_data:
        ctx.runtime.run("volume", "rm", render.POSTGRES_VOLUME, check=False)
        shutil.rmtree(ctx.paths.root, ignore_errors=True)
        ok(f"removed {ctx.paths.root}")
    else:
        info(f"data kept at {ctx.paths.root} (use --purge-data to delete)")


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    """Check prerequisites and configuration."""
    paths = LocalPaths()
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "ok": passed, "detail": detail})

    state = detect.detect()
    for cli, flags in state.items():
        detail = "running" if flags["running"] else ("installed, not running" if flags["installed"] else "not installed")
        check(f"runtime:{cli}", flags["installed"], detail)
    check("config", paths.config_file.exists(), str(paths.config_file))
    check("release", paths.release_file.exists(), str(paths.release_file))

    if paths.config_file.exists():
        config = store.load(paths)
        provider = store.provider(config)
        check("provider-selected", state[provider]["running"], f"{provider} responding")
        overrides = store.env_overrides(config, "backend")
        has_key = bool(
            overrides.get("LEMMA_OPENAI_API_KEY") or overrides.get("LEMMA_ANTHROPIC_API_KEY")
        )
        check("llm-api-key", has_key, "set" if has_key else "no LLM key configured")
        for name in ("frontend", "backend", "agentbox"):
            port = store.port(config, name)
            running = False
            if state[provider]["running"]:
                try:
                    runtime = detect.ensure_ready(provider)
                    running = runtime.container_running(f"{CONTAINER_PREFIX}-{name}")
                except AdminError:
                    running = False
            in_use = _port_in_use(port)
            # a port used by our own running container is fine
            check(
                f"port:{port}",
                (not in_use) or running,
                f"{name} ({'own stack' if running else 'free' if not in_use else 'IN USE by another process'})",
            )

    if json_output:
        print_json({"checks": checks, "ok": all(c["ok"] for c in checks)})
        return
    for c in checks:
        marker = "[green]ok[/green]" if c["ok"] else "[red]fail[/red]"
        console.print(f"{marker} {c['name']}: {c['detail']}")
    if not all(c["ok"] for c in checks):
        raise typer.Exit(1)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


@config_app.command("list")
def config_list(
    json_output: bool = typer.Option(False, "--json"),
    show_secrets: bool = typer.Option(False, "--show-secrets"),
) -> None:
    """List all configuration values."""
    paths = LocalPaths()
    doc = store.load(paths)
    flat = store.flatten(doc)
    if not show_secrets:
        flat = {key: store.redact(key, value) for key, value in flat.items()}
    if json_output:
        print_json(flat)
        return
    for key, value in flat.items():
        console.print(f"{key} = {value!r}")


@config_app.command("get")
def config_get(key: str) -> None:
    doc = store.load(LocalPaths())
    console.print(store.get_value(doc, key))


@config_app.command("set")
def config_set(
    pairs: list[str] = typer.Argument(..., help="KEY=VALUE pairs (or: KEY VALUE for one key)."),
) -> None:
    """Set config values; bare UPPER_SNAKE keys go to [backend.env]."""
    paths = LocalPaths()
    doc = store.load(paths)
    if len(pairs) == 2 and "=" not in pairs[0]:
        pairs = [f"{pairs[0]}={pairs[1]}"]
    for pair in pairs:
        if "=" not in pair:
            raise AdminError(f"expected KEY=VALUE, got {pair!r}")
        key, _, value = pair.partition("=")
        parts = store.set_value(doc, key.strip(), value)
        ok(f"set {'.'.join(parts)}")
    store.save(paths, doc)
    info("restart required to apply: lemma-stack restart")


@config_app.command("unset")
def config_unset(key: str) -> None:
    paths = LocalPaths()
    doc = store.load(paths)
    store.unset_value(doc, key)
    store.save(paths, doc)
    ok(f"unset {key}")


@config_app.command("edit")
def config_edit() -> None:
    """Open config.toml in $EDITOR and validate the result."""
    import os
    import subprocess

    import tomlkit

    paths = LocalPaths()
    store.load(paths)  # ensure it exists
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(paths.config_file)], check=False)
    try:
        tomlkit.parse(paths.config_file.read_text(encoding="utf-8"))
    except Exception as exc:  # tomlkit raises several parse error types
        raise AdminError(f"config.toml is no longer valid TOML: {exc}")
    ok("config valid; run `lemma-stack restart` to apply")


@config_app.command("path")
def config_path() -> None:
    console.print(str(LocalPaths().config_file))


# --------------------------------------------------------------------------
# passthrough
# --------------------------------------------------------------------------


@db_app.command("shell")
def db_shell() -> None:
    """Open psql against the stack's postgres."""
    ctx = _load_context()
    raise typer.Exit(
        ctx.runtime.stream("exec", "-it", f"{CONTAINER_PREFIX}-db", "psql", "-U", "postgres", "lemma")
    )


@db_app.command("sql")
def db_sql(
    query: str,
    database: str = typer.Option("lemma", "--database", "-d"),
) -> None:
    """Run one SQL statement and print the result."""
    ctx = _load_context()
    proc = ctx.runtime.run(
        "exec", f"{CONTAINER_PREFIX}-db", "psql", "-U", "postgres", "-d", database, "-c", query
    )
    console.print(proc.stdout)


@db_app.command("url")
def db_url() -> None:
    """Print the in-network database URL (for `lemma-stack db sql` style access)."""
    console.print("postgresql://postgres:postgres@db:5432/lemma (network: lemma-local-net)")


@redis_app.command("cli")
def redis_cli(args: list[str] = typer.Argument(None)) -> None:
    """Run redis-cli inside the stack's redis container."""
    ctx = _load_context()
    raise typer.Exit(
        ctx.runtime.stream("exec", "-it", f"{CONTAINER_PREFIX}-redis", "redis-cli", *(args or []))
    )


# --------------------------------------------------------------------------
# self
# --------------------------------------------------------------------------


@self_app.command("version")
def self_version() -> None:
    console.print(f"lemma-stack {__version__}")
    paths = LocalPaths()
    if paths.release_file.exists():
        console.print(f"stack {release_manifest.load_pinned(paths).version}")


@self_app.command("info")
def self_info(json_output: bool = typer.Option(False, "--json")) -> None:
    paths = LocalPaths()
    payload = {
        "admin_version": __version__,
        "root": str(paths.root),
        "config": str(paths.config_file),
        "stack_version": None,
        "runtimes": detect.detect(),
    }
    if paths.release_file.exists():
        payload["stack_version"] = release_manifest.load_pinned(paths).version
    if json_output:
        print_json(payload)
    else:
        for key, value in payload.items():
            console.print(f"{key}: {value}")


def main() -> None:
    enrich_path()
    try:
        app()
    except AdminError as exc:
        raise fail(str(exc))


if __name__ == "__main__":
    main()
