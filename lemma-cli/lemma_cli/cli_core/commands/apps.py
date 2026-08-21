from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import typer
from lemma_sdk.openapi_client.models.create_app_request import CreateAppRequest
from lemma_sdk.openapi_client.models.update_app_request import UpdateAppRequest

from ..context import selected_org, selected_pod
from ..project_env import write_server_env
from ..app_scaffold import (
    DEFAULT_TEMPLATE_SOURCE,
    AppScaffoldOptions,
    app_name_from_path,
    load_search_config,
    normalize_chat_mode,
    normalize_navigation,
    normalize_style_preset,
    scaffold_app,
    scaffold_html_app,
    title_from_name,
)
from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..payload import read_json
from ..sdk import pod_client
from ..state import console, fail, run_with_client, state_from_ctx
from lemma_sdk.config import resolve_auth_url, resolve_base_url, resolve_token
from ...cli_app.app_bundle import (
    classify_app_source,
    deploy_app_bundle,
    extract_app_source_archive,
)

app = typer.Typer(help="App commands.")


def _bundle_root(directory: Path) -> Path:
    """The pod-bundle root (nearest ancestor with ``pod.json``) to bind, else the
    given directory itself. Bounded by the git root / filesystem root."""
    directory = directory.resolve()
    current = directory
    while True:
        if (current / "pod.json").is_file():
            return current
        if (current / ".git").exists() or current.parent == current:
            break
        current = current.parent
    return directory


def _app_browser_url(url: str, *, env_key: str, log: bool = True) -> str:
    parsed = urlparse(url)
    if parsed.hostname != "host.docker.internal":
        return url

    netloc = "localhost"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    browser_url = urlunparse(parsed._replace(netloc=netloc))
    if log:
        console.print(
            "[yellow]Warning:[/yellow] "
            f"{env_key} uses {browser_url!r} because {url!r} is only reachable "
            "from inside Docker."
        )
    return browser_url


@app.command("list")
def list_apps(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List apps in the pod."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).apps.list(limit=limit),
    )
    if result is not None:
        emit(state, result)


@app.command("get")
def get_app(
    ctx: typer.Context,
    app: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show an app."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).apps.get(app)
    )
    if result is not None:
        emit(state, result)


def _origin(url: str) -> str:
    """Return the scheme://host[:port] origin of a URL (no path)."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def _run_agent_browser(commands: list[list[str]], *, best_effort: bool = False) -> None:
    """Drive agent-browser with the command list on stdin, never in argv.

    Process arguments are world-readable on Linux -- any other local user can
    read them from `ps` or /proc/<pid>/cmdline while the call runs, and execve
    auditing records them for good afterwards. That makes argv the wrong place
    for a session bearer token. `agent-browser batch` accepts the whole
    sequence as JSON on stdin, so nothing sensitive reaches the process table.
    """
    result = subprocess.run(
        ["agent-browser", "batch", "--bail"],
        input=json.dumps(commands),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 or best_effort:
        return

    # `--bail` stops at the first failure, so the tail of its own output is the
    # reason. Relaying it beats the bare exit code the previous loop reported.
    reason = (result.stderr or result.stdout or "").strip().splitlines()
    detail = f": {reason[-1]}" if reason else ""
    fail(f"agent-browser exited with code {result.returncode}{detail}.")


@app.command("open")
def open_app(
    ctx: typer.Context,
    app: str | None = typer.Argument(
        None, help="App slug/name to open (resolves its served URL)."
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Open an explicit URL (e.g. a local dev server http://localhost:5173) "
        "instead of resolving an app by name.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
    no_auth: bool = typer.Option(
        False,
        "--no-auth",
        help="Skip token injection (e.g. a `npm run dev` app already "
        "self-authenticates via the dev token).",
    ),
) -> None:
    """Open an app in the agent browser, authenticated as the current agent/user.

    Seeds the current access token into the app's ``localStorage`` (the browser
    SDK's ``injectedToken`` mode — the only signal it honours for its auth check)
    and also registers it as an ``Authorization: Bearer`` header scoped to the API
    origin, then opens the app. Use for a *deployed* app; a local ``npm run dev``
    app already self-authenticates via the dev token, so pass ``--no-auth`` with
    ``--url http://localhost:5173``.
    """
    state = state_from_ctx(ctx)
    if bool(app) == bool(url):
        fail("Provide an app slug or --url (exactly one).")

    if shutil.which("agent-browser") is None:
        fail("agent-browser is not installed or not on PATH.")

    if url:
        app_url = url
    else:
        detail = run_with_client(
            ctx, lambda client, s: pod_client(client, s, pod).apps.get(app)
        )
        if detail is None:
            return
        app_url = getattr(detail, "url", None) or to_plain(detail).get("url")
        if not app_url:
            fail(f"App {app!r} has no served URL yet — deploy it first.")

    commands: list[list[str]] = []
    cleanup: list[list[str]] = []
    if not no_auth:
        use_env = state.server_source == "env"
        try:
            token = resolve_token(state.token, state.config, use_env=use_env)
        except ValueError as exc:
            fail(str(exc))
            return
        api_origin = _origin(
            resolve_base_url(state.base_url, state.config, use_env=use_env)
        )
        auth_origin = _origin(
            resolve_auth_url(state.auth_url, state.config, use_env=use_env)
        )
        headers = json.dumps({"Authorization": f"Bearer {token}"})
        # Register the bearer scoped to the API origin (agent-browser keeps it for
        # that origin and does not leak it to other origins the app loads) so the
        # app's direct, non-SDK API calls carry the header.
        commands.append(["open", api_origin, "--headers", headers])
        # The browser SDK only treats the session as authenticated when it finds a
        # token in localStorage ("injectedToken" mode); it never reads the bearer
        # header for its auth check, and in cookie mode it short-circuits to
        # "unauthenticated" before ever calling /users/me. So seed lemma_token into
        # the app's own origin, mirroring the Vite dev-server pattern. First block
        # any redirect to the auth portal, so a cookie-mode app can't bounce us off
        # its origin before the token lands; lift the block once it's seeded.
        if auth_origin and auth_origin != api_origin:
            commands.append(["network", "route", f"{auth_origin}/**", "--abort"])
            cleanup.append(["network", "unroute", f"{auth_origin}/**"])
        commands.append(["open", app_url])
        commands.append(["storage", "local", "set", "lemma_token", token])
        commands.append(["reload"])
    else:
        commands.append(["open", app_url])

    _run_agent_browser(commands)
    # Lift the temporary auth-portal block (best-effort; don't fail the open if it
    # errors) so later agent-browser commands in this session aren't blocked.
    if cleanup:
        _run_agent_browser(cleanup, best_effort=True)

    emit(state, {"ok": True, "url": app_url, "authenticated": not no_auth})


@app.command("create")
def create_app(
    ctx: typer.Context,
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create an app from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).apps.create(
            CreateAppRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("update")
def update_app(
    ctx: typer.Context,
    app: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Update an app from a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).apps.update(
            app, UpdateAppRequest.from_dict(payload)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("delete")
def delete_app(
    ctx: typer.Context,
    app: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete an app."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete app {app}?", yes)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).apps.delete(app)
    )
    if result is None:
        emit(state, {"ok": True})


@app.command("deploy")
def deploy_app(
    ctx: typer.Context,
    app: str = typer.Argument(...),
    source: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=True,
        dir_okay=True,
        readable=True,
        help=(
            "App source: a single .html file, a static dir (index.html), or a "
            "Vite project dir (package.json). Defaults to the current directory."
        ),
    ),
    source_dir: Path | None = typer.Option(
        None,
        "--source-dir",
        hidden=True,
        exists=True,
        file_okay=True,
        dir_okay=True,
        readable=True,
        help="Deprecated alias for the SOURCE argument.",
    ),
    dist_dir: Path | None = typer.Option(
        None,
        "--dist-dir",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Prebuilt dist directory. Skips npm/pnpm/yarn build.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
    create: bool = typer.Option(
        True,
        "--create/--no-create",
        help="Create a minimal app if it does not already exist.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the interactive deployment confirmation.",
    ),
) -> None:
    """Build and deploy an app bundle.

    Point SOURCE at a single .html file, a prebuilt static dir, or a Vite
    project. HTML/static sources need no build and no VITE_LEMMA_* env — the
    host injects pod context at serve time.
    """
    state = state_from_ctx(ctx)
    # `--source-dir` is the legacy flag; the positional SOURCE supersedes it.
    source_dir = source_dir if source_dir is not None else source

    def run(client, s):  # type: ignore[no-untyped-def]
        from .pods import resolve_pod_id

        pod_id = resolve_pod_id(client, s, pod)
        pod_label = pod_id
        try:
            pod_payload = to_plain(client.pods.get(pod_id))
            pod_name = (
                pod_payload.get("name") if isinstance(pod_payload, dict) else None
            )
            if pod_name:
                pod_label = f"{pod_name} ({pod_id})"
        except Exception:
            pass

        tier = classify_app_source(source_dir)

        if not yes:
            console.print("[bold]App deploy[/bold]")
            console.print(f"App: {app}")
            console.print(f"Pod: {pod_label}")
            console.print(f"Source: {source_dir} ({tier})")
            # Pod context (pod id, API/auth URLs) is injected by the host as
            # window.__LEMMA_CONFIG__ at serve time, so the build carries no baked
            # pod and runs on whichever pod it's deployed to.
            console.print(
                "Pod context is injected by the host at serve time — the build is "
                "pod-agnostic (VITE_LEMMA_* in .env.local is dev-only)."
            )
            if not typer.confirm("Continue with deploy?"):
                fail("Deploy cancelled.", code=0)

        return deploy_app_bundle(
            client,
            pod_id=pod_id,
            app_name=app,
            source_dir=source_dir,
            dist_dir=dist_dir,
            ensure_exists=create,
        )

    result = run_with_client(
        ctx,
        run,
    )
    if result is not None:
        emit(state, result)


@app.command("pull")
def pull_app(
    ctx: typer.Context,
    app: str = typer.Argument(...),
    target: Path | None = typer.Argument(
        None,
        file_okay=False,
        dir_okay=True,
        writable=True,
        help="Directory to write the source into. Defaults to ./<app>.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite files in a target directory that is not empty.",
    ),
) -> None:
    """Download a deployed app's source into a local directory.

    The inverse of ``deploy``: ``deploy`` uploads project source alongside the
    built dist, and this brings that source back so an app can be edited from
    a machine (or a workspace sandbox) that never held the original checkout.
    Vite apps arrive without ``node_modules``, so run an install before
    building.
    """
    state = state_from_ctx(ctx)
    target_dir = target if target is not None else Path(app)

    def run(client, s):  # type: ignore[no-untyped-def]
        archive = pod_client(client, s, pod).apps.download_source_archive(app)
        try:
            written = extract_app_source_archive(archive, target_dir, overwrite=force)
        except ValueError as error:
            fail(str(error))
        return {
            "app": app,
            "target": str(target_dir.expanduser().resolve()),
            "files": len(written),
        }

    result = run_with_client(ctx, run)
    if result is not None:
        emit(state, result)


@app.command("init")
def init_app(
    ctx: typer.Context,
    directory: Path = typer.Argument(
        Path("."), help="Directory to initialize. Must be empty."
    ),
    name: str | None = typer.Option(None, "--name", "--app-name"),
    pod: str | None = typer.Option(
        None, "--pod", help="Pod id or slug to use for this app."
    ),
    title: str | None = typer.Option(None, "--title"),
    html: bool = typer.Option(
        False,
        "--html",
        help=(
            "Scaffold a single no-build index.html wired to the host-served SDK "
            "instead of a full Vite project. Skips the template clone and install."
        ),
    ),
    nav: str = typer.Option("sidebar", "--nav", help="sidebar, topbar, single-page"),
    agent: str | None = typer.Option(
        None, "--agent", help="Agent name to wire into the optional chat surface."
    ),
    chat_mode: str = typer.Option(
        "right-sidebar", "--chat-mode", help="page, popup, right-sidebar"
    ),
    members: bool = typer.Option(False, "--members/--no-members"),
    search_config: str | None = typer.Option(
        None, "--search-config", help="JSON or @file for Lemma global search."
    ),
    theme_toggle: bool = typer.Option(True, "--theme-toggle/--no-theme-toggle"),
    style: str = typer.Option(
        "soft",
        "--style",
        "--theme",
        help="Template style preset: neobrutal, editorial, soft, terminal.",
    ),
    template: str = typer.Option(
        DEFAULT_TEMPLATE_SOURCE,
        "--template",
        "--template-source",
        help=(
            "App starter template. Defaults to the bundled offline starter; "
            "pass a git URL or local path to override."
        ),
    ),
    sdk_path: str | None = typer.Option(
        None,
        "--sdk-path",
        help=(
            "Local checkout of the lemma-sdk npm package to depend on "
            "(file: path). Auto-detected from a sibling lemma-typescript "
            "checkout when omitted."
        ),
    ),
    install: bool = typer.Option(
        True,
        "--install/--no-install",
        help="Install dependencies after writing files.",
    ),
    proxy: bool = typer.Option(
        False,
        "--proxy/--no-proxy",
        help=(
            "Serve API calls through a same-origin '/api' dev proxy (no CORS in "
            "dev). Off by default; the dev server is otherwise allowed in backend "
            "CORS."
        ),
    ),
    registry: bool = typer.Option(
        False,
        "--registry/--no-registry",
        help="Deprecated. Registry scaffolding is no longer installed by app init.",
    ),
) -> None:
    """Scaffold a new app project locally."""
    state = state_from_ctx(ctx)

    if html:
        try:
            app_name = name or app_name_from_path(directory)
            steps = scaffold_html_app(
                directory, title=title or title_from_name(app_name)
            )
        except (OSError, ValueError) as exc:
            fail(str(exc))
            return
        if state.output == "json":
            emit(
                state,
                {
                    "directory": str(directory.resolve()),
                    "name": app_name,
                    "mode": "html",
                    "steps": steps,
                },
            )
            return
        for step in steps:
            console.print(f"[green]✓[/green] {step}")
        console.print()
        console.print(f"[bold]HTML app ready:[/bold] {directory.resolve()}")
        console.print(
            f"Next: edit index.html, then "
            f"`lemma apps deploy {app_name} {directory}/index.html`"
        )
        return

    try:
        selected_pod_id = pod or selected_pod(state, required=False)
        if not selected_pod_id:
            fail("No pod selected. Pass --pod or run `lemma pods` first.")
        app_name = name or app_name_from_path(directory)
        base_url = resolve_base_url(
            state.base_url,
            state.config,
            use_env=state.server_source == "env",
        )
        auth_url = resolve_auth_url(
            state.auth_url,
            state.config,
            use_env=state.server_source == "env",
        )
        log_rewrite = state.output != "json"
        options = AppScaffoldOptions(
            target_dir=directory,
            name=app_name,
            pod_id=selected_pod_id,
            api_url=_app_browser_url(
                base_url, env_key="VITE_LEMMA_API_URL", log=log_rewrite
            ),
            auth_url=_app_browser_url(
                auth_url, env_key="VITE_LEMMA_AUTH_URL", log=log_rewrite
            ),
            title=title or title_from_name(app_name),
            navigation=normalize_navigation(nav),
            agent_name=agent,
            chat_mode=normalize_chat_mode(chat_mode),
            members=members,
            search_config=load_search_config(search_config),
            theme_toggle=theme_toggle,
            install=install,
            registry=registry,
            style_preset=normalize_style_preset(style),
            template_source=template,
            sdk_path=sdk_path,
            proxy=proxy,
        )
        steps = scaffold_app(options)
    except (OSError, ValueError) as exc:
        fail(str(exc))
        return

    # Bind the folder to this pod on the active server so later `lemma` commands
    # target it automatically. Skipped under the read-only env server (a workspace sandbox).
    binding_path: Path | None = None
    if not state.server_read_only:
        values = {"LEMMA_POD_ID": selected_pod_id}
        org_id = selected_org(state, required=False)
        if org_id:
            values["LEMMA_ORG_ID"] = org_id
        try:
            binding_path = write_server_env(
                _bundle_root(options.target_dir), state.server, values
            )
        except OSError:
            binding_path = None

    if state.output == "json":
        emit(
            state,
            {
                "directory": str(options.target_dir.resolve()),
                "name": options.name,
                "steps": steps,
                "binding": str(binding_path) if binding_path else None,
            },
        )
        return

    for step in steps:
        console.print(f"[green]✓[/green] {step}")
    if binding_path:
        console.print(
            f"[green]✓[/green] Bound to pod on server '{state.server}' "
            f"([dim]{binding_path}[/dim])"
        )
    console.print()
    console.print(f"[bold]App scaffold ready:[/bold] {options.target_dir.resolve()}")
    console.print(f"Next: cd {options.target_dir.resolve()} && npm run dev")
