"""lemma-stack: install and manage a fully-local Lemma stack."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from lemma_stack import __version__, host_pack, orchestrate
from lemma_stack.config import render, store
from lemma_stack.context import AdminContext
from lemma_stack.locald_client import LocaldClient, LocaldError
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
from lemma_stack.register import install_lemma_cli_and_skills, register_local_server
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


def _managed_locald() -> LocaldClient | None:
    """Prefer the installed managed runtime unless an expert opts out."""

    import os

    if os.environ.get("LEMMA_STACK_FORCE_EXTERNAL_RUNTIME") == "1":
        return None
    return LocaldClient.discover()


def _managed_request(client: LocaldClient, command: str, **payload):
    try:
        return client.request(command, **payload)
    except LocaldError as error:
        raise AdminError(str(error)) from error


def _managed_operator(client: LocaldClient) -> dict:
    return _managed_request(client, "control.snapshot").get("operator") or {}


def _flatten_json(value, prefix: str = "") -> dict[str, object]:
    flattened: dict[str, object] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_json(child, child_prefix))
    else:
        flattened[prefix] = value
    return flattened


def _json_value(root: dict, key: str):
    current = root
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise AdminError(f"unknown managed configuration key: {key}")
        current = current[part]
    return current


def _set_managed_value(config: dict, key: str, raw: str) -> None:
    if key in {
        "schema_version",
        "install_id",
        "revision",
        "ai.models",
        "ai.last_validated_at_unix_ms",
    }:
        raise AdminError(f"managed configuration key is read-only: {key}")
    parts = key.split(".")
    parent = config
    for part in parts[:-1]:
        child = parent.get(part)
        if not isinstance(child, dict):
            raise AdminError(f"unknown managed configuration key: {key}")
        parent = child
    leaf = parts[-1]
    if leaf not in parent:
        raise AdminError(f"unknown managed configuration key: {key}")
    current = parent[leaf]
    if isinstance(current, bool):
        normalized = raw.strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no"}:
            raise AdminError(f"{key} expects true or false")
        parent[leaf] = normalized in {"true", "1", "yes"}
    elif isinstance(current, list):
        parent[leaf] = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(current, str):
        parent[leaf] = raw
    else:
        raise AdminError(f"managed configuration key is not editable: {key}")


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
            f"  {step}. Model provider  [red](required — agents won't run without one; "
            "set the type + key together)[/red]"
        )
        console.print("       [dim]# Anthropic (Claude):[/dim]")
        console.print("       lemma-stack config set LEMMA_DEFAULT_MODEL_TYPE anthropic_compat")
        console.print("       lemma-stack config set LEMMA_ANTHROPIC_API_KEY sk-ant-...")
        console.print(
            "       [dim]# or any OpenAI-compatible provider (OpenAI, Fireworks, local, …):[/dim]"
        )
        console.print("       lemma-stack config set LEMMA_DEFAULT_MODEL_TYPE openai_compat")
        console.print("       lemma-stack config set LEMMA_OPENAI_API_KEY <key>")
        console.print(
            "       lemma-stack config set LEMMA_OPENAI_BASE_URL https://api.openai.com/v1"
        )
        console.print("       lemma-stack config set LEMMA_OPENAI_DEFAULT_MODEL gpt-4o")
        console.print("       lemma-stack config set LEMMA_OPENAI_MODEL_NAMES gpt-4o,gpt-4o-mini")
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
    channel: Optional[str] = typer.Option(
        None, help="Release channel or version (default: stable)."
    ),
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
    for name in ("frontend", "backend"):
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
        images.pull_release(runtime, manifest)
        release_manifest.pin(paths, manifest)

    # 7. install the lemma CLI and point it at this stack (server "local")
    if use_cli:
        install_lemma_cli_and_skills(version=manifest.version)
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
            "  [dim]the lemma.localhost domain is reserved for loopback; sign-in and "
            "host-routed apps are scoped to it, so use the URLs shown above[/dim]"
        )

    _print_next_steps(config)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


@app.command()
def prepare() -> None:
    """Prepare the app-owned host runtime (one-time Windows setup if needed)."""
    client = _managed_locald()
    if client is None:
        raise AdminError(
            "no managed Lemma Desktop runtime is installed; open Lemma Desktop to install it"
        )
    event = _managed_request(client, "runtime.prepare")
    if event.get("reboot_required"):
        warn("restart Windows, then reopen Lemma; local setup will continue automatically")
    elif event.get("ready"):
        ok("local runtime prerequisites are ready")
    else:
        raise AdminError("local runtime preparation did not report a usable state")


@app.command()
def start() -> None:
    """Start (or reconcile) the installed stack."""
    if client := _managed_locald():
        _managed_request(client, "start")
        state = _managed_request(client, "status")
        info(f"app: {state.get('url') or 'http://app.lemma.localhost:3711'}")
        return
    ctx = _load_context()
    lifecycle.up(ctx.runtime, ctx.specs(), ctx.manifest, migrate=False)
    info(f"app: {render.frontend_origin(ctx.config)}")


@app.command()
def stop(
    infra: bool = typer.Option(False, "--infra", help="Also stop db/redis/supertokens."),
) -> None:
    """Stop the stack (app services; --infra stops everything)."""
    if client := _managed_locald():
        _managed_request(client, "stop", infra=infra)
        return
    ctx = _load_context()
    specs = ctx.specs()
    if not infra:
        specs = [s for s in specs if s.name in {"backend", "frontend"}]
    lifecycle.down(ctx.runtime, specs)


@app.command()
def restart() -> None:
    """Restart the stack, re-rendering config (apply config.toml changes)."""
    if client := _managed_locald():
        _managed_request(client, "restart")
        state = _managed_request(client, "status")
        info(f"app: {state.get('url') or 'http://app.lemma.localhost:3711'}")
        return
    ctx = _load_context()
    specs = ctx.specs()
    lifecycle.down(ctx.runtime, specs)
    lifecycle.up(ctx.runtime, specs, ctx.manifest, migrate=False)


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show the state of every managed service or external-runtime container."""
    if client := _managed_locald():
        event = _managed_request(client, "status")
        payload = {
            "version": event.get("release"),
            "provider": "managed-local",
            "root": str(client.root),
            "status": event.get("status"),
            "ready": event.get("ready", False),
            "services": event.get("components", []),
            "managed_runtime": event.get("managed_runtime"),
            "url": event.get("url"),
            "api_url": event.get("api_url"),
        }
        if json_output:
            print_json(payload)
            return
        state = "[green]ready[/green]" if payload["ready"] else "[yellow]not ready[/yellow]"
        info(f"Lemma {payload['version'] or '-'} (managed-local) — {client.root} — {state}")
        table = Table()
        for column in ("service", "status", "pid", "restarts"):
            table.add_column(column)
        for service in payload["services"]:
            running = bool(service.get("running"))
            service_state = "[green]running[/green]" if running else "[red]stopped[/red]"
            if service.get("circuit_open"):
                service_state = "[red]failed[/red]"
            table.add_row(
                str(service.get("id", "-")),
                service_state,
                str(service.get("pid") or "-"),
                str(service.get("restart_count", 0)),
            )
        console.print(table)
        managed = payload.get("managed_runtime") or {}
        if managed:
            info(
                "private runtime: "
                f"{managed.get('engine', '-')} via {managed.get('endpoint_host', '-')}"
            )
        return
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
        state = (
            f"[green]{row['status']}[/green]" if row["running"] else f"[red]{row['status']}[/red]"
        )
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


@app.command("host-manifest", hidden=True)
def host_manifest(
    pack_root: Path = typer.Option(..., "--pack-root"),
    output: Optional[Path] = typer.Option(None, "--output"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    manifest_path: Optional[Path] = typer.Option(None, "--release-manifest"),
) -> None:
    """Render the private two-process manifest consumed by lemma-locald."""

    paths = LocalPaths()
    paths.ensure()
    config = store.load_or_create(paths)
    bundled_manifest = pack_root / "release.json"
    if manifest_path is not None or bundled_manifest.is_file():
        release = release_manifest.load_file(manifest_path or bundled_manifest)
        release_manifest.pin(paths, release)
    else:
        release = release_manifest.load_pinned(paths)
    selected_provider = provider or store.provider(config)
    if selected_provider not in {"docker", "podman", "lemma_local"}:
        raise AdminError(f"unsupported host-pack provider: {selected_provider}")
    destination = output or paths.run_dir / "host-pack.json"
    manifest = host_pack.build_manifest(
        pack_root,
        paths,
        config,
        release,
        provider=selected_provider,
    )
    host_pack.write_manifest(destination, manifest)
    console.print(str(destination))


@app.command()
def logs(
    service: str = typer.Argument(..., help="One of: db, redis, supertokens, backend, frontend."),
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(200, "--lines"),
) -> None:
    """Tail logs of one managed host service or external-runtime container."""
    if client := _managed_locald():
        managed_logs = {
            "locald": client.root / "locald.log",
            "backend": client.root / "logs/backend.log",
            "frontend": client.root / "logs/frontend.log",
        }
        if service not in managed_logs:
            raise AdminError(
                "managed logs supports locald, backend, or frontend; "
                "private infrastructure diagnostics are available in Control Center"
            )
        _tail_file(managed_logs[service], lines=max(0, min(lines, 10_000)), follow=follow)
        return
    ctx = _load_context()
    args = ["logs", "--tail", str(lines)]
    if follow:
        args.append("-f")
    raise typer.Exit(ctx.runtime.stream(*args, f"{CONTAINER_PREFIX}-{service}"))


def _tail_file(path: Path, *, lines: int, follow: bool) -> None:
    import time

    if not path.is_file():
        raise AdminError(f"log is not available yet: {path}")
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        block = 8192
        chunks: list[str] = []
        while position > 0 and sum(chunk.count("\n") for chunk in chunks) <= lines:
            read_size = min(block, position)
            position -= read_size
            handle.seek(position)
            chunks.append(handle.read(read_size))
        recent = "".join(reversed(chunks)).splitlines()[-lines:] if lines else []
        for line in recent:
            console.print(line, markup=False)
        if not follow:
            return
        handle.seek(0, 2)
        try:
            while True:
                line = handle.readline()
                if line:
                    console.print(line.rstrip("\r\n"), markup=False)
                else:
                    time.sleep(0.2)
        except KeyboardInterrupt:
            return


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

    if client := _managed_locald():
        snapshot = _managed_request(client, "control.snapshot")
        services = snapshot.get("services") or []
        runtime = snapshot.get("managed_runtime") or {}
        readiness = (snapshot.get("operator") or {}).get("readiness") or {}
        check("locald", True, str(client.root))
        check(
            "managed-runtime",
            bool(runtime.get("engine")),
            str(runtime.get("engine") or "not started"),
        )
        for service in services:
            running = bool(service.get("running")) and not bool(service.get("circuit_open"))
            detail = (
                "running"
                if running
                else ("restart circuit open" if service.get("circuit_open") else "stopped")
            )
            check(f"service:{service.get('id', 'unknown')}", running, detail)
        ai_ready = readiness.get("ai") == "ready"
        check(
            "ai-provider",
            ai_ready,
            "validated" if ai_ready else "needs setup in Lemma Control Center",
        )
        payload = {
            "provider": "managed-local",
            "checks": checks,
            "ok": all(c["ok"] for c in checks),
        }
        if json_output:
            print_json(payload)
            return
        for item in checks:
            marker = "[green]ok[/green]" if item["ok"] else "[red]fail[/red]"
            console.print(f"{marker} {item['name']}: {item['detail']}")
        if not payload["ok"]:
            raise typer.Exit(1)
        return

    state = detect.detect()
    for cli, flags in state.items():
        detail = (
            "running"
            if flags["running"]
            else ("installed, not running" if flags["installed"] else "not installed")
        )
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
        for name in ("frontend", "backend"):
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
    if client := _managed_locald():
        if show_secrets:
            raise AdminError("managed secrets are write-only and cannot be displayed")
        operator = _managed_operator(client)
        flat = _flatten_json(operator.get("config") or {})
        for name, present in (operator.get("secrets") or {}).items():
            flat[name] = "<configured>" if present else "<not configured>"
        if json_output:
            print_json(flat)
            return
        for key, value in flat.items():
            console.print(f"{key} = {value!r}")
        return
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
    if client := _managed_locald():
        operator = _managed_operator(client)
        secrets = operator.get("secrets") or {}
        if key in secrets:
            console.print("<configured>" if secrets[key] else "<not configured>")
            return
        console.print(_json_value(operator.get("config") or {}, key))
        return
    doc = store.load(LocalPaths())
    console.print(store.get_value(doc, key))


@config_app.command("set")
def config_set(
    pairs: list[str] = typer.Argument(..., help="KEY=VALUE pairs (or: KEY VALUE for one key)."),
) -> None:
    """Set config values; bare UPPER_SNAKE keys go to [backend.env]."""
    if client := _managed_locald():
        operator = _managed_operator(client)
        config = operator.get("config") or {}
        known_secrets = operator.get("secrets") or {}
        changes: dict[str, str | None] = {}
        if len(pairs) == 2 and "=" not in pairs[0]:
            pairs = [f"{pairs[0]}={pairs[1]}"]
        for pair in pairs:
            if "=" not in pair:
                raise AdminError(f"expected KEY=VALUE, got {pair!r}")
            key, _, value = pair.partition("=")
            key = key.strip()
            if key in known_secrets:
                changes[key] = value
            else:
                _set_managed_value(config, key, value)
            ok(f"set {key}")
        _managed_request(client, "config.apply", payload={"config": config, "secrets": changes})
        return
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
    if client := _managed_locald():
        operator = _managed_operator(client)
        config = operator.get("config") or {}
        known_secrets = operator.get("secrets") or {}
        changes: dict[str, str | None] = {}
        if key in known_secrets:
            changes[key] = None
        elif key == "ai.protocol":
            config["ai"].update(
                {
                    "protocol": "unconfigured",
                    "base_url": "",
                    "default_model": "",
                    "models": [],
                    "vision_models": [],
                    "last_validated_at_unix_ms": None,
                }
            )
        else:
            current = _json_value(config, key)
            if isinstance(current, bool):
                _set_managed_value(config, key, "false")
            elif isinstance(current, (str, list)):
                _set_managed_value(config, key, "")
            else:
                raise AdminError(f"managed configuration key is not editable: {key}")
        _managed_request(client, "config.apply", payload={"config": config, "secrets": changes})
        ok(f"unset {key}")
        return
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

    if _managed_locald():
        raise AdminError(
            "managed configuration is transactional and secrets are stored in the OS vault; "
            "use `lemma-stack config set` or Lemma Control Center"
        )
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
    if client := _managed_locald():
        console.print(str(client.root / "operator-config.json"))
        info("secret values are stored separately in the OS credential vault")
        return
    console.print(str(LocalPaths().config_file))


# --------------------------------------------------------------------------
# passthrough
# --------------------------------------------------------------------------


@db_app.command("shell")
def db_shell() -> None:
    """Open psql against the stack's postgres."""
    ctx = _load_context()
    raise typer.Exit(
        ctx.runtime.stream(
            "exec", "-it", f"{CONTAINER_PREFIX}-db", "psql", "-U", "postgres", "lemma"
        )
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
    managed = _managed_locald()
    payload = {
        "admin_version": __version__,
        "root": str(paths.root),
        "config": str(paths.config_file),
        "stack_version": None,
        "managed_local": {
            "installed": managed is not None,
            "root": str(managed.root) if managed else None,
            "binary": str(managed.binary) if managed else None,
        },
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
