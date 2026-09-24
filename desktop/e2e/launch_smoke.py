"""Launch the built Lemma Desktop app and walk one journey through it.

This is the check that the *app* starts. Every other Desktop gate builds it,
lints it, or drives one of its halves in a browser; none of them opens the
bundle and watches it run. What this proves, in order:

1. the workspace stack comes up: this checkout's backend (API and worker) and
   lemma-frontend, against the Postgres, Redis and SuperTokens it is handed;
2. an owner signs up through the auth endpoints the sign-up page posts to, and
   gets an organisation and a pod;
3. this machine is paired the way the workspace pairs it: a code minted with
   the owner's session, handed to the bundle's own ``lemma-locald`` as
   ``agent-host.pair`` -- the message the ``agent_host_pair`` Tauri command
   sends;
4. the app binary launches in hosted mode against that workspace, stays up,
   and its WKWebView loads the workspace (the workspace origin is a recording
   relay only the app is pointed at, so a request there is the app's);
5. because the machine is paired, the app itself starts ``lemma-locald`` at
   launch, and locald starts the bundled Agent Host, which links to the
   backend and publishes a scripted ACP agent as ready;
6. a conversation with that agent streams the scripted answer back, and it is
   persisted once, from one provider prompt;
7. the Agent Host status the This Mac settings card is built from -- locald's
   ``agent-host.status``, which the ``agent_host_status`` command returns --
   names this backend's host id as connected, which is how the card
   recognises the computer you are sitting at.

What it does not do, and why:

* **No Virtualization.framework guest.** GitHub's macOS runners cannot run a
  nested VM, so the local connection mode -- locald booting the private
  runtime and supervising the host pack inside it -- is out of reach, and the
  app is launched in hosted mode instead. Postgres, Redis and SuperTokens are
  native processes on the runner, and the backend and frontend run from this
  checkout rather than from a packed host pack.
* **No clicks in the WebView.** Nothing drives WKWebView from outside on
  macOS (tauri-driver is Linux and Windows only), so signing up, sending the
  message and opening Settings with ⌘, go through the HTTP API and the locald
  messages the page would send. The page's own rendering of the chat and of
  Settings → This Mac is covered in Chromium by the Desktop contracts lane and
  ``desktop/ui-tests``.

Run it through the backend's environment, which has httpx and Python 3.14::

    uv run --project lemma-backend python desktop/e2e/launch_smoke.py \\
        --app desktop/target/debug/bundle/macos/Lemma.app

``--sidecars desktop/binaries`` runs everything except step 4, with locald
started by this script instead of by the app -- for a machine that has the
sidecars but not the bundle. Infrastructure comes from
``LEMMA_SMOKE_DATABASE_URL``, ``LEMMA_SMOKE_REDIS_URL`` and
``LEMMA_SMOKE_SUPERTOKENS_URL``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import plistlib
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from uuid import uuid4

import httpx

REPOSITORY = Path(__file__).resolve().parents[2]
BACKEND = REPOSITORY / "lemma-backend"
FRONTEND = REPOSITORY / "lemma-frontend"
FIXTURES = REPOSITORY / "desktop/agent-host/tests/fixtures"
TRIPLE = f"{platform.machine().replace('arm64', 'aarch64')}-apple-darwin"
ANSWER = "前 café 👩🏽‍💻\nsecond line\n完成"

# A locald event is JSON with no fixed shape beyond ``event`` and ``id``.
LocaldEvent = dict[str, Any]


class SmokeFailure(RuntimeError):
    pass


def step(message: str) -> None:
    print(f"[smoke {time.strftime('%H:%M:%S')}] {message}", flush=True)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def until[T](
    label: str,
    probe: Callable[[], Awaitable[T | None]],
    *,
    timeout: float,
    interval: float = 0.5,
) -> T:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            value = await probe()
        except (httpx.HTTPError, OSError, ValueError, KeyError) as error:
            last_error = f"({type(error).__name__}: {error})"
            value = None
        if value is not None:
            return value
        await asyncio.sleep(interval)
    raise SmokeFailure(
        f"timed out after {timeout:.0f}s waiting for {label} {last_error}"
    )


@dataclass
class Processes:
    """Everything this run started, stopped from one place whatever happens."""

    logs: Path
    running: list[tuple[str, subprocess.Popen[bytes]]] = field(default_factory=list)
    handles: list[IO[bytes]] = field(default_factory=list)

    def start(
        self, name: str, command: list[str], *, env: dict[str, str], cwd: Path
    ) -> subprocess.Popen[bytes]:
        log = (self.logs / f"{name}.log").open("ab")
        self.handles.append(log)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.running.append((name, process))
        return process

    def forget(self, process: subprocess.Popen[bytes]) -> None:
        self.running = [entry for entry in self.running if entry[1] is not process]

    def stop_all(self) -> None:
        for _, process in reversed(self.running):
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        for _, process in reversed(self.running):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for handle in self.handles:
            handle.close()

    def assert_alive(self) -> None:
        for name, process in self.running:
            if process.poll() is not None:
                raise SmokeFailure(
                    f"{name} exited with {process.returncode}; "
                    f"see {self.logs / (name + '.log')}"
                )


class RecordingRelay:
    """A TCP relay in front of the frontend that notes every request line.

    The app's WebView is the only client of this port, so a request here is
    proof that the shell navigated its window to the workspace.
    """

    def __init__(self, target_port: int) -> None:
        self.port = free_port()
        self.requests: list[str] = []
        self._target = target_port
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._relay, host="127.0.0.1", port=self.port
        )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()

    async def _relay(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", self._target
            )
        except OSError:
            client_writer.close()
            return

        async def pump(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter, note: bool
        ) -> None:
            try:
                while chunk := await reader.read(65536):
                    if note:
                        line = chunk.split(b"\r\n", 1)[0].decode("latin-1")
                        if " HTTP/" in line:
                            self.requests.append(line)
                    writer.write(chunk)
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        await asyncio.gather(
            pump(client_reader, upstream_writer, True),
            pump(upstream_reader, client_writer, False),
        )


def backend_environment(
    *, api_port: int, site_url: str, scratch: Path
) -> dict[str, str]:
    database_url = os.environ["LEMMA_SMOKE_DATABASE_URL"]
    return {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        "API_URL": f"http://127.0.0.1:{api_port}",
        "FRONTEND_URL": site_url,
        "AUTH_FRONTEND_URL": site_url,
        "CORS_ORIGIN_REGEX": r"^http://127[.]0[.]0[.]1:[0-9]+$",
        "DATABASE_URL": database_url,
        "DATASTORE_DATABASE_URL": database_url,
        "REDIS_URL": os.environ["LEMMA_SMOKE_REDIS_URL"],
        "SUPERTOKENS_CORE_URL": os.environ["LEMMA_SMOKE_SUPERTOKENS_URL"],
        "STORAGE_BACKEND": "local",
        "LOCAL_FILE_STORAGE_ROOT": str(scratch / "files"),
        "LOCAL_OBJECT_STORAGE_ROOT": str(scratch / "objects"),
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": str(scratch / "email"),
        "LOCAL_EMBEDDING_PRELOAD": "false",
        "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED": "false",
        "AUTH_EMAIL_VERIFICATION_REQUIRED": "false",
        "AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED": "false",
        "AUTH_ABUSE_PROTECTION_ENABLED": "false",
        "AUTH_ALTCHA_ENABLED": "false",
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": "desktop-launch-smoke-credential-key",
    }


def frontend_environment(*, api_url: str, site_url: str) -> dict[str, str]:
    # Read by lemma-frontend's server when it starts and handed to the page by
    # /site-config.js, the same way frontend-launcher.mjs configures it.
    return {
        **os.environ,
        "NEXT_TELEMETRY_DISABLED": "1",
        "LEMMA_FRONTEND_HOST": "127.0.0.1",
        "NEXT_PUBLIC_DATA": "live",
        "NEXT_PUBLIC_API_URL": api_url,
        "NEXT_PUBLIC_SITE_URL": site_url,
        "NEXT_PUBLIC_AUTH_URL": f"{site_url}/auth",
        "NEXT_PUBLIC_LEMMA_DEPLOYMENT": "local",
        "NEXT_PUBLIC_ANALYTICS_KEY": "",
    }


def scripted_agent(root: Path) -> tuple[Path, Path]:
    """A ``cursor-agent`` on the host's search path that is the scripted agent."""
    shims = root / "shim-bin"
    shims.mkdir()
    traffic = root / "acp-stream.jsonl"
    # Released from the start: nobody here waits on the first line.
    traffic.with_suffix(".release").write_text("continue")
    agent = shlex.join(
        [
            sys.executable,
            str(FIXTURES / "scripted_acp_agent.py"),
            str(traffic),
            f"json:{FIXTURES / 'scenarios/stream.json'}",
        ]
    )
    shim = shims / "cursor-agent"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "--version) echo '2026.7.31' ;;\n"
        f"*) exec {agent} ;;\n"
        "esac\n"
    )
    shim.chmod(0o700)
    return shims, traffic


@dataclass(frozen=True)
class Install:
    """One installation's state, laid out the way the app lays it out."""

    support: Path
    locald: Path
    agent_host: Path | None
    extra_env: dict[str, str]

    @property
    def locald_root(self) -> Path:
        return self.support / "locald"

    def locald_env(self) -> dict[str, str]:
        environment = {
            **os.environ,
            **self.extra_env,
            "LEMMA_DESKTOP": "1",
            "LEMMA_LOCALD_ROOT": str(self.locald_root),
        }
        if self.agent_host is not None:
            environment["LEMMA_AGENT_HOST_BIN"] = str(self.agent_host)
        return environment

    def send(self, command: str, **fields: str) -> LocaldEvent:
        """One control request; returns the event that answered it."""
        request = {"cmd": command, "id": f"smoke-{uuid4().hex[:8]}", **fields}
        result = subprocess.run(
            [str(self.locald), "send", json.dumps(request)],
            env=self.locald_env(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        answers = [
            event
            for event in (
                json.loads(line)
                for line in result.stdout.splitlines()
                if line.startswith("{")
            )
            if event.get("id") == request["id"]
        ]
        if result.returncode != 0 or not answers or answers[-1].get("event") == "error":
            raise SmokeFailure(
                f"locald {command} failed ({result.returncode}): "
                f"{result.stdout[-2000:]} {result.stderr[-2000:]}"
            )
        return answers[-1]


def bundle_executable(app: Path) -> Path:
    with (app / "Contents/Info.plist").open("rb") as plist:
        return app / "Contents/MacOS" / plistlib.load(plist)["CFBundleExecutable"]


async def sign_up_owner(api: httpx.AsyncClient) -> str:
    form = {
        "formFields": [
            {"id": "email", "value": f"smoke+{uuid4().hex[:10]}@example.com"},
            {"id": "password", "value": "Desktop-smoke-1"},
        ]
    }
    signed_up = await api.post("/st/auth/signup", json=form)
    if signed_up.json().get("status") != "OK":
        raise SmokeFailure(f"sign-up refused: {signed_up.text}")
    signed_in = await api.post("/st/auth/signin", json=form)
    token = signed_in.headers.get("st-access-token") or signed_in.cookies.get(
        "sAccessToken"
    )
    if signed_in.json().get("status") != "OK" or not token:
        raise SmokeFailure(f"sign-in refused: {signed_in.text}")
    return token


async def created(
    api: httpx.AsyncClient, path: str, body: dict[str, str | list[str] | dict[str, str]]
) -> str:
    response = await api.post(path, json=body)
    if not response.is_success:
        raise SmokeFailure(f"POST {path} -> {response.status_code}: {response.text}")
    return str(response.json()["id"])


async def ask(api: httpx.AsyncClient, conversation: str) -> str:
    text = ""
    last = ""
    async with api.stream(
        "POST", f"{conversation}/messages", json={"content": "Say the script."}
    ) as response:
        if not response.is_success:
            raise SmokeFailure(f"send failed: {(await response.aread())!r}")
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            frame = json.loads(line.removeprefix("data: "))
            last = frame.get("type", "")
            if last == "token" and frame.get("kind") == "text":
                text += frame["data"]
            if last in {"completed", "error", "stopped"}:
                break
    if last != "completed":
        raise SmokeFailure(f"the run ended {last!r} after {text!r}")
    return text


@dataclass
class Stack:
    api_url: str
    site_url: str
    frontend_port: int
    relay: RecordingRelay


async def start_stack(
    arguments: argparse.Namespace, root: Path, processes: Processes
) -> Stack:
    api_port, frontend_port = free_port(), free_port()
    relay = RecordingRelay(frontend_port)
    await relay.start()
    site_url = f"http://127.0.0.1:{relay.port}"
    api_url = f"http://127.0.0.1:{api_port}"
    backend_env = backend_environment(
        api_port=api_port, site_url=site_url, scratch=root / "backend"
    )
    python = str(BACKEND / ".venv/bin/python")

    step("migrating the database")
    migrate = await asyncio.to_thread(
        subprocess.run,
        [python, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=backend_env,
        capture_output=True,
        text=True,
        check=False,
    )
    (processes.logs / "migrations.log").write_text(migrate.stdout + migrate.stderr)
    if migrate.returncode != 0:
        raise SmokeFailure(
            f"migrations failed; see {processes.logs / 'migrations.log'}"
        )

    step("starting the API, the worker and the frontend")
    processes.start(
        "api",
        [
            python,
            "-m",
            "uvicorn",
            "app.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--ws",
            "websockets-sansio",
        ],
        env=backend_env,
        cwd=BACKEND,
    )
    processes.start(
        "worker", [python, "-m", "app.worker"], env=backend_env, cwd=BACKEND
    )
    node = shutil.which("node") or "node"
    frontend = [node, "server.mjs", "--port", str(frontend_port)]
    if arguments.frontend_dev:
        frontend.insert(2, "--dev")
    processes.start(
        "frontend",
        frontend,
        env=frontend_environment(api_url=api_url, site_url=site_url),
        cwd=FRONTEND,
    )

    async def serving(url: str) -> bool | None:
        processes.assert_alive()
        async with httpx.AsyncClient(timeout=30) as probe:
            response = await probe.get(url)
        return True if response.status_code < 500 else None

    await until("the API", lambda: serving(f"{api_url}/health"), timeout=180)
    await until(
        "the frontend",
        lambda: serving(f"http://127.0.0.1:{frontend_port}/"),
        timeout=240,
    )
    step("stack healthy")
    return Stack(api_url, site_url, frontend_port, relay)


async def pair_this_machine(
    api: httpx.AsyncClient, install: Install, api_url: str, processes: Processes
) -> subprocess.Popen[bytes]:
    """Pair through locald, as the workspace's automatic connection does."""
    minted = await api.post(
        "/me/runtime/agent-host-pairings", json={"display_name": "Smoke Mac"}
    )
    if not minted.is_success:
        raise SmokeFailure(f"could not mint a pairing code: {minted.text}")
    install.support.mkdir()
    daemon = processes.start(
        "locald",
        [str(install.locald), "serve"],
        env=install.locald_env(),
        cwd=install.support,
    )

    async def answering() -> LocaldEvent | None:
        processes.assert_alive()
        try:
            return await asyncio.to_thread(install.send, "status")
        except SmokeFailure:
            # Refused until the daemon has written its token and bound its
            # socket, which is the thing being waited for.
            return None

    await until("locald to answer", answering, timeout=60)
    await asyncio.to_thread(
        install.send,
        "agent-host.pair",
        url=api_url,
        pairing_code=minted.json()["pairing_code"],
        name="Smoke Mac",
    )
    step("this machine paired through locald")
    return daemon


async def launch_app(
    app: Path,
    install: Install,
    stack: Stack,
    daemon: subprocess.Popen[bytes],
    processes: Processes,
) -> None:
    # The daemon that paired is stopped, so bringing one up at launch is the
    # app's job: it starts locald itself when this machine is paired.
    await asyncio.to_thread(install.send, "shutdown-daemon")
    await asyncio.to_thread(daemon.wait, 30)
    processes.forget(daemon)
    step("launching the app in hosted mode")
    processes.start(
        "app",
        [str(bundle_executable(app))],
        env={
            **os.environ,
            **install.extra_env,
            "LEMMA_DESKTOP_APP_SUPPORT_DIR": str(install.support),
            "LEMMA_DESKTOP_CONNECTION_MODE": "hosted",
            "LEMMA_DESKTOP_HOSTED_URL": stack.site_url,
        },
        cwd=install.support,
    )

    async def webview_loaded() -> str | None:
        processes.assert_alive()
        return stack.relay.requests[0] if stack.relay.requests else None

    first = await until(
        "the app's WebView to load the workspace", webview_loaded, timeout=120
    )
    step(f"the app's WebView loaded the workspace: {first}")


async def converse(
    api: httpx.AsyncClient,
    org_id: str,
    pod_id: str,
    traffic: Path,
    processes: Processes,
) -> str:
    async def ready_harness() -> tuple[str, str] | None:
        processes.assert_alive()
        hosts = (await api.get("/me/runtime/agent-hosts")).json()["items"]
        if len(hosts) != 1:
            return None
        listing = await api.get(f"/me/runtime/agent-hosts/{hosts[0]['id']}/harnesses")
        for item in listing.json()["items"]:
            if item["harness_key"] == "cursor" and item["health"] == "READY":
                return hosts[0]["id"], item["id"]
        return None

    host_id, harness_id = await until(
        "the Agent Host to publish the scripted agent", ready_harness, timeout=120
    )
    step("the Agent Host linked and published its agent")
    profile_id = await created(
        api,
        f"/organizations/{org_id}/agent-runtime/profiles",
        {"source": "AGENT_HOST", "name": "Smoke agent", "harness_id": harness_id},
    )
    agent_name = f"smoke_{uuid4().hex[:8]}"
    await created(
        api,
        f"/pods/{pod_id}/agents",
        {
            "name": agent_name,
            "instruction": "Reply directly.",
            "toolsets": [],
            "agent_runtime": {"profile_id": profile_id},
        },
    )
    conversation_id = await created(
        api,
        f"/pods/{pod_id}/conversations",
        {"agent_name": agent_name, "title": "Smoke"},
    )
    conversation = f"/pods/{pod_id}/conversations/{conversation_id}"
    answer = await ask(api, conversation)
    if answer != ANSWER:
        raise SmokeFailure(f"streamed {answer!r}, expected {ANSWER!r}")

    async def persisted_once() -> bool | None:
        items = (await api.get(f"{conversation}/messages")).json()["items"]
        texts = [
            item.get("text")
            for item in items
            if item["role"] == "assistant" and item["kind"] == "TEXT"
        ]
        return True if texts == [ANSWER] else None

    await until("the answer to be persisted once", persisted_once, timeout=30)
    prompts = sum(
        json.loads(line)["message"].get("method") == "session/prompt"
        for line in traffic.read_text().splitlines()
    )
    if prompts != 1:
        raise SmokeFailure(f"the provider was prompted {prompts} times")
    step("the conversation streamed the scripted answer and kept it")
    return host_id


async def this_mac_is_connected(install: Install, host_id: str) -> None:
    seen: list[LocaldEvent] = []

    async def connected() -> LocaldEvent | None:
        status = (await asyncio.to_thread(install.send, "agent-host.status"))[
            "agent_host"
        ]
        seen[:] = [status]
        mine = [t for t in status.get("targets", []) if t.get("host_id") == host_id]
        if (
            status.get("running")
            and mine
            and mine[0].get("connection_state") == "ONLINE"
        ):
            return status
        return None

    try:
        await until(
            "This Mac to report this workspace connected", connected, timeout=60
        )
    except SmokeFailure as failure:
        raise SmokeFailure(f"{failure}; last status: {json.dumps(seen)}") from None
    step("This Mac status names this workspace's host, connected")


async def journey(arguments: argparse.Namespace, root: Path, logs: Path) -> None:
    processes = Processes(logs)
    stack: Stack | None = None
    install: Install | None = None
    try:
        stack = await start_stack(arguments, root, processes)
        async with httpx.AsyncClient(base_url=stack.api_url, timeout=60) as api:
            api.headers["Authorization"] = f"Bearer {await sign_up_owner(api)}"
            org_id = await created(api, "/organizations", {"name": "Smoke org"})
            pod_id = await created(
                api, "/pods", {"organization_id": org_id, "name": "Smoke pod"}
            )
            step("owner signed up; organisation and pod created")

            shims, traffic = scripted_agent(root)
            extra_env = {
                "LEMMA_AGENT_HOST_PATH": str(shims),
                "LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD": "1",
            }
            support = root / "support"
            if arguments.app is not None:
                locald = arguments.app / "Contents/MacOS/lemma-locald"
                install = Install(support, locald, None, extra_env)
            else:
                install = Install(
                    support,
                    arguments.sidecars / f"lemma-locald-{TRIPLE}",
                    arguments.sidecars / f"lemma-agent-host-{TRIPLE}",
                    extra_env,
                )
            daemon = await pair_this_machine(api, install, stack.api_url, processes)
            if arguments.app is not None:
                await launch_app(arguments.app, install, stack, daemon, processes)
            host_id = await converse(api, org_id, pod_id, traffic, processes)
            await this_mac_is_connected(install, host_id)
            processes.assert_alive()
    finally:
        if install is not None and install.locald_root.exists():
            with suppress(SmokeFailure, subprocess.TimeoutExpired, OSError):
                install.send("shutdown-daemon")
        processes.stop_all()
        if stack is not None:
            await stack.relay.close()
        # locald's children are not ours to track by pid; anything still
        # naming this run's private root is a leftover of it.
        await asyncio.to_thread(subprocess.run, ["pkill", "-f", str(root)], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app", type=Path, help="the built Lemma.app")
    source.add_argument(
        "--sidecars", type=Path, help="desktop/binaries, to run all but the app itself"
    )
    parser.add_argument(
        "--frontend-dev", action="store_true", help="next dev, not a build"
    )
    parser.add_argument(
        "--artifacts", type=Path, default=REPOSITORY / "output/desktop-launch-smoke"
    )
    arguments = parser.parse_args()
    # Absolute, because the binaries under them are started with the private
    # support directory as their working directory, where a path relative to
    # the caller's no longer names anything.
    for name in ("app", "sidecars", "artifacts"):
        if getattr(arguments, name) is not None:
            setattr(arguments, name, getattr(arguments, name).resolve())
    for name in (
        "LEMMA_SMOKE_DATABASE_URL",
        "LEMMA_SMOKE_REDIS_URL",
        "LEMMA_SMOKE_SUPERTOKENS_URL",
    ):
        if not os.environ.get(name):
            parser.error(f"{name} is required")
    arguments.artifacts.mkdir(parents=True, exist_ok=True)
    # Short on purpose: locald's control socket lives under this root, and a
    # Unix socket path is capped at 104 bytes on macOS.
    root = Path(tempfile.mkdtemp(prefix="lemma-smoke-", dir="/tmp"))
    started = time.monotonic()
    try:
        asyncio.run(journey(arguments, root, arguments.artifacts))
    except SmokeFailure as failure:
        step(f"FAILED: {failure}")
        return 1
    finally:
        for log in root.rglob("*.log"):
            shutil.copy(log, arguments.artifacts / f"{log.parent.name}-{log.name}")
        shutil.rmtree(root, ignore_errors=True)
    step(f"passed in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
