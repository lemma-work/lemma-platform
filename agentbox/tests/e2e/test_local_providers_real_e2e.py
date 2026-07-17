from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any
from urllib import error, parse, request
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]
LOCAL_PROVIDERS = ("docker", "podman")
FUNCTION_CONCURRENCY = 8
FUNCTION_RUN_COUNT = 20
WORKSPACE_ROOT = "/workspace"
CONVERSATION_ROOT = f"{WORKSPACE_ROOT}/c/2026-07-15"


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_cli(provider: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [provider, *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _require_real_provider(provider: str) -> None:
    if not shutil.which(provider):
        pytest.skip(f"real {provider} e2e skipped: {provider} CLI is not installed")
    probe = _run_cli(provider, "info")
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        reason = detail[-1] if detail else "provider daemon is unavailable"
        pytest.skip(f"real {provider} e2e skipped: {reason}")


def _runtime_image(provider: str, repo_root: Path) -> str:
    configured = os.getenv(f"AGENTBOX_E2E_{provider.upper()}_IMAGE")
    image = configured or f"agentbox-runtime:e2e-{provider}"
    # An explicitly supplied image is an immutable CI/release input. The local
    # default is always rebuilt from the current checkout so a stale tag cannot
    # produce a false provider pass after runtime code changes.
    if configured and _run_cli(provider, "image", "inspect", image).returncode == 0:
        return image
    build = _run_cli(
        provider,
        "build",
        "-f",
        str(repo_root / "agentbox" / "Dockerfile.runtime"),
        "-t",
        image,
        str(repo_root),
    )
    if build.returncode != 0:
        pytest.fail(
            f"real {provider} runtime image build failed\n"
            f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
        )
    return image


@dataclass(frozen=True)
class _HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str

    def json(self) -> dict[str, Any]:
        value = json.loads(self.text or "{}")
        assert isinstance(value, dict)
        return value


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ManagerClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> _HttpResponse:
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            **(headers or {}),
        }
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        return self.raw(
            method,
            f"{self.base_url}{path}",
            data=payload,
            headers=request_headers,
            timeout=timeout,
        )

    def raw(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 120,
    ) -> _HttpResponse:
        req = request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with request.build_opener(_NoRedirect).open(
                req, timeout=timeout
            ) as response:
                return _HttpResponse(
                    response.status,
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read().decode(errors="replace"),
                )
        except error.HTTPError as exc:
            try:
                content = exc.read().decode(errors="replace")
            finally:
                exc.close()
            return _HttpResponse(
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()},
                content,
            )


@dataclass
class _RealProviderServer:
    provider: str
    base_url: str
    api_key: str
    app_domain: str

    @property
    def client(self) -> _ManagerClient:
        return _ManagerClient(self.base_url, self.api_key)

    def cleanup(self, sandbox_id: str) -> None:
        self.client.request("DELETE", f"/sandboxes/{sandbox_id}", timeout=60)

    def public_get(
        self, public_url: str, *, cookie: str | None = None
    ) -> _HttpResponse:
        parsed = parse.urlsplit(public_url)
        path = parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = {"Host": parsed.netloc}
        if cookie:
            headers["Cookie"] = cookie
        return self.client.raw(
            "GET",
            f"{self.base_url}{path}",
            headers=headers,
            timeout=120,
        )

    def provider_identity(self, sandbox_id: str) -> str:
        inspect = _run_cli(
            self.provider,
            "inspect",
            "--format",
            "{{.Id}}",
            f"agentbox-{sandbox_id}",
        )
        assert inspect.returncode == 0, inspect.stderr
        return inspect.stdout.strip()


@pytest.fixture(scope="module", params=LOCAL_PROVIDERS)
def real_local_provider_server(
    request: pytest.FixtureRequest,
    repo_root: Path,
) -> Generator[_RealProviderServer, None, None]:
    provider = str(request.param)
    _require_real_provider(provider)

    image = _runtime_image(provider, repo_root)

    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    api_key = f"agentbox-{provider}-real-e2e"
    app_domain = f"127-0-0-1.sslip.io:{port}"
    # Podman Machine shares the user's home directory on macOS, while /tmp is
    # inside the VM. A home-backed temporary bind makes the test portable to
    # both remote Podman and a native Linux daemon.
    scratch_parent = Path.home() / ".cache" / "agentbox-real-provider-e2e"
    scratch_parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=f"{provider}-", dir=scratch_parent) as tmpdir:
        tmp_path = Path(tmpdir)
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root / "agentbox"),
            "AGENTBOX_PROVIDER": provider,
            "AGENTBOX_API_KEY": api_key,
            "AGENTBOX_API_URL": base_url,
            "AGENTBOX_APP_DOMAIN": app_domain,
            "AGENTBOX_RUNTIME_IMAGE": image,
            "AGENTBOX_STATE_DB_PATH": str(tmp_path / "state.db"),
            "AGENTBOX_STORAGE_ROOT": str(tmp_path / "workspaces"),
            "AGENTBOX_ENDPOINT_HOST": "127.0.0.1",
            "AGENTBOX_E2E_LABEL": "true",
            "AGENTBOX_FUNCTION_MAX_CONCURRENCY": str(FUNCTION_CONCURRENCY),
            "AGENTBOX_FUNCTION_MAX_QUEUED": "32",
            "AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS": "300",
            "AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS": "8",
            "AGENTBOX_CLEANUP_INTERVAL_SECONDS": "1",
            "AGENTBOX_RECONCILE_INTERVAL_SECONDS": "1",
            "AGENTBOX_SUSPENDED_RETENTION_SECONDS": "120",
            "AGENTBOX_SANDBOX_READY_TIMEOUT_SECONDS": "300",
        }
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentbox.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=repo_root,
            env=env,
            # Let pytest capture the manager output directly. PIPE without a
            # concurrent reader eventually fills during the 20-call function
            # test and blocks the manager in a log write, making healthy HTTP
            # endpoints appear deadlocked.
        )
        server = _RealProviderServer(provider, base_url, api_key, app_domain)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(
                        f"{provider} manager exited during startup "
                        f"with status {proc.returncode}; see captured output"
                    )
                try:
                    health = server.client.raw("GET", f"{base_url}/health", timeout=2)
                    if health.status_code == HTTPStatus.OK:
                        payload = health.json()
                        assert payload["provider"] == provider
                        break
                except error.URLError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail(f"timed out starting real {provider} manager")
            yield server
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.fixture
def local_sandbox_id(
    request: pytest.FixtureRequest,
    real_local_provider_server: _RealProviderServer,
) -> Generator[str, None, None]:
    test_name = request.node.name.lower().replace("_", "-")[:24]
    sandbox_id = (
        f"real-{real_local_provider_server.provider}-{test_name}-{uuid4().hex[:8]}"
    )
    yield sandbox_id
    real_local_provider_server.cleanup(sandbox_id)


def _ensure(
    server: _RealProviderServer,
    sandbox_id: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    response = server.client.request(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": env or {}},
        timeout=360,
    )
    assert response.status_code == HTTPStatus.OK, response.text
    assert response.json()["sandbox"] == {
        "id": sandbox_id,
        "ready": True,
        "status": "RUNNING",
    }


def _create_session(
    server: _RealProviderServer,
    sandbox_id: str,
    session_id: str,
    *,
    cwd: str | None = None,
) -> None:
    session_cwd = cwd or f"{CONVERSATION_ROOT}/{session_id}"
    response = server.client.request(
        "PUT",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}",
        body={"env": {"SESSION_MARK": session_id}, "cwd": session_cwd},
        timeout=120,
    )
    assert response.status_code == HTTPStatus.OK, response.text


def _python(
    server: _RealProviderServer,
    sandbox_id: str,
    session_id: str,
    code: str,
    *,
    timeout: int = 30,
) -> _HttpResponse:
    return server.client.request(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
        body={"code": code, "timeout_seconds": timeout},
        timeout=timeout + 30,
    )


def _exec(
    server: _RealProviderServer,
    sandbox_id: str,
    session_id: str,
    body: dict[str, Any],
) -> _HttpResponse:
    return server.client.request(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body=body,
        timeout=float(body.get("timeout", 30)) + 30,
    )


def test_real_local_provider_runtime_sessions_commands_and_processes(
    real_local_provider_server: _RealProviderServer,
    local_sandbox_id: str,
) -> None:
    server = real_local_provider_server
    sandbox_id = local_sandbox_id
    durable_runtime_mark = f"https://{server.provider}.provider-e2e.invalid"
    _ensure(server, sandbox_id, env={"LEMMA_BASE_URL": durable_runtime_mark})

    health = server.client.request(
        "GET", f"/sandboxes/{sandbox_id}/apps/runtime/health"
    )
    assert health.status_code == HTTPStatus.OK, health.text
    assert health.json() == {"status": "ok"}

    first_session = "python-state"
    second_session = "python-isolated"
    _create_session(server, sandbox_id, first_session)
    _create_session(server, sandbox_id, second_session)

    state = _python(server, sandbox_id, first_session, "counter = 41\ncounter")
    assert state.status_code == HTTPStatus.OK, state.text
    assert state.json()["result"] == "41"
    state = _python(server, sandbox_id, first_session, "counter += 1\ncounter")
    assert state.json()["result"] == "42", state.text
    isolated = _python(server, sandbox_id, second_session, "'counter' in globals()")
    assert isolated.json()["result"] == "False"

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _python,
                server,
                sandbox_id,
                session_id,
                "import time\ntime.sleep(2)\n'finished'",
                timeout=10,
            )
            for session_id in (first_session, second_session)
        ]
        parallel = [future.result() for future in futures]
    elapsed = time.monotonic() - started
    assert all(item.status_code == HTTPStatus.OK for item in parallel)
    assert all(item.json()["result"] == "'finished'" for item in parallel)
    assert elapsed < 3.5, f"different Python sessions serialized for {elapsed:.2f}s"

    cwd_and_env = _exec(
        server,
        sandbox_id,
        first_session,
        {
            "cmd": 'pwd; printf \'%s:%s\\n\' "$SESSION_MARK" "$LEMMA_BASE_URL"',
            "timeout": 30,
        },
    )
    assert cwd_and_env.status_code == HTTPStatus.OK, cwd_and_env.text
    assert cwd_and_env.json()["success"] is True
    assert f"{CONVERSATION_ROOT}/{first_session}" in cwd_and_env.json()["stdout"]
    assert f"{first_session}:{durable_runtime_mark}" in cwd_and_env.json()["stdout"]

    interactive = _exec(
        server,
        sandbox_id,
        first_session,
        {
            "cmd": "read line; printf 'stdin:%s\\n' \"$line\"",
            "yield_time_ms": 250,
            "timeout": 30,
        },
    )
    assert interactive.status_code == HTTPStatus.OK, interactive.text
    assert interactive.json()["completed"] is False
    process_id = interactive.json()["process_id"]
    stdin = server.client.request(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{first_session}/stdin",
        body={
            "process_id": process_id,
            "chars": "hello-provider\n",
            "yield_time_ms": 1000,
        },
    )
    assert stdin.status_code == HTTPStatus.OK, stdin.text
    assert stdin.json()["completed"] is True
    assert "stdin:hello-provider" in stdin.json()["stdout"]

    tty = _exec(
        server,
        sandbox_id,
        first_session,
        {
            "cmd": (
                "python -c 'import sys; print(sys.stdin.isatty(), sys.stdout.isatty())'"
            ),
            "tty": True,
            "yield_time_ms": 1000,
            "timeout": 30,
        },
    )
    assert tty.status_code == HTTPStatus.OK, tty.text
    assert "True True" in tty.json()["stdout"]

    sleeper = _exec(
        server,
        sandbox_id,
        first_session,
        {"cmd": "sleep 60", "yield_time_ms": 250, "timeout": 90},
    )
    assert sleeper.json()["completed"] is False
    sleeper_id = sleeper.json()["process_id"]
    listed = server.client.request(
        "GET", f"/sandboxes/{sandbox_id}/sessions/{first_session}/processes"
    )
    assert listed.status_code == HTTPStatus.OK, listed.text
    assert any(
        row["process_id"] == sleeper_id and row["completed"] is False
        for row in listed.json()["processes"]
    )
    terminated = server.client.request(
        "DELETE",
        f"/sandboxes/{sandbox_id}/sessions/{first_session}/processes/{sleeper_id}",
    )
    assert terminated.status_code == HTTPStatus.OK, terminated.text
    assert terminated.json()["completed"] is True


@dataclass(frozen=True)
class _FakeFunction:
    pod_id: str
    function_id: str
    name: str
    token: str
    user_id: str
    organization_id: str
    code: str


class _FunctionHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        function: _FakeFunction = self.server.function  # type: ignore[attr-defined]
        if self.headers.get("Authorization") != f"Bearer {function.token}":
            self._send(HTTPStatus.UNAUTHORIZED, {"detail": "bad token"})
            return
        path = parse.urlsplit(self.path).path
        if path == "/auth/verify-token":
            self._send(
                HTTPStatus.OK,
                {
                    "user_id": function.user_id,
                    "email": "real-provider-e2e@example.com",
                    "pod_id": function.pod_id,
                    "organization_id": function.organization_id,
                    "function_id": function.function_id,
                    "function_name": function.name,
                    "scopes": ["function:execute"],
                },
            )
            return
        expected = (
            f"/pods/{function.pod_id}/functions/{parse.quote(function.name, safe='')}"
        )
        if path == expected:
            self._send(
                HTTPStatus.OK,
                {
                    "id": function.function_id,
                    "name": function.name,
                    "pod_id": function.pod_id,
                    "type": "API",
                    "code": function.code,
                },
            )
            return
        self._send(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.fixture
def concurrent_function_server(
    real_local_provider_server: _RealProviderServer,
) -> Generator[tuple[str, _FakeFunction], None, None]:
    name = f"provider_concurrency_{uuid4().hex[:8]}"
    code = f'''#input_type_name: ConcurrencyInput
#output_type_name: ConcurrencyOutput
#function_name: {name}

import asyncio
import os
import time
from pydantic import BaseModel

class ConcurrencyInput(BaseModel):
    marker: str
    delay: float = 2.0

class ConcurrencyOutput(BaseModel):
    marker: str
    pid: int
    started: float
    finished: float
    shared_marker: str

async def {name}(ctx, data: ConcurrencyInput) -> ConcurrencyOutput:
    from pathlib import Path
    workspace = Path("{CONVERSATION_ROOT}/function-execution")
    workspace.mkdir(parents=True, exist_ok=True)
    shared_marker = (workspace / "runtime-sentinel.txt").read_text()
    invocation_marker = workspace / (data.marker + ".txt")
    started = time.monotonic()
    await asyncio.sleep(data.delay)
    invocation_marker.write_text(data.marker)
    finished = time.monotonic()
    print(f"{{data.marker}}:{{os.getpid()}}:{{started}}:{{finished}}")
    return ConcurrencyOutput(
        marker=data.marker,
        pid=os.getpid(),
        started=started,
        finished=finished,
        shared_marker=shared_marker,
    )
'''
    function = _FakeFunction(
        pod_id=str(uuid4()),
        function_id=str(uuid4()),
        name=name,
        token=f"real-provider-token-{uuid4().hex}",
        user_id=str(uuid4()),
        organization_id=str(uuid4()),
        code=code,
    )
    port = _available_port()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _FunctionHandler)
    httpd.function = function  # type: ignore[attr-defined]
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host = (
        "host.containers.internal"
        if real_local_provider_server.provider == "podman"
        else "host.docker.internal"
    )
    try:
        yield f"http://{host}:{port}", function
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _execute_function(
    server: _RealProviderServer,
    sandbox_id: str,
    function: _FakeFunction,
    run_id: str,
    *,
    async_job: bool,
) -> _HttpResponse:
    return server.client.request(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": run_id,
            "input_data": {"marker": run_id, "delay": 2.0},
            "async_job": async_job,
            "timeout_seconds": 60,
        },
        headers={"Authorization": f"Bearer {function.token}"},
        timeout=120,
    )


def _wait_for_job(
    server: _RealProviderServer,
    sandbox_id: str,
    run_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = server.client.request(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}",
            timeout=30,
        )
        assert response.status_code == HTTPStatus.OK, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "timeout"}:
            return payload
        time.sleep(0.2)
    pytest.fail(f"function job {run_id} did not finish")


def _peak_function_overlap(outputs: list[dict[str, Any]]) -> int:
    events: list[tuple[float, int]] = []
    for output in outputs:
        events.append((float(output["started"]), 1))
        events.append((float(output["finished"]), -1))
    active = 0
    peak = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        active += delta
        peak = max(peak, active)
    return peak


def test_real_local_provider_runs_twenty_mixed_api_and_job_functions_concurrently(
    real_local_provider_server: _RealProviderServer,
    local_sandbox_id: str,
    concurrent_function_server: tuple[str, _FakeFunction],
) -> None:
    server = real_local_provider_server
    sandbox_id = local_sandbox_id
    lemma_base_url, function = concurrent_function_server
    _ensure(server, sandbox_id, env={"LEMMA_BASE_URL": lemma_base_url})
    session_id = "function-execution"
    _create_session(server, sandbox_id, session_id)
    seed_workspace = _exec(
        server,
        sandbox_id,
        session_id,
        {
            "cmd": "printf runtime-to-function > runtime-sentinel.txt",
            "timeout": 30,
        },
    )
    assert seed_workspace.status_code == HTTPStatus.OK, seed_workspace.text
    assert seed_workspace.json()["success"] is True

    run_ids = [str(uuid4()) for _ in range(FUNCTION_RUN_COUNT)]
    # Ten API and ten JOB calls enter one eight-slot executor together. This
    # exercises real queueing as well as process isolation on both local
    # providers; an eight-call batch would only prove the active slots.
    async_modes = [index % 2 == 1 for index in range(FUNCTION_RUN_COUNT)]
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=FUNCTION_RUN_COUNT) as pool:
        futures = [
            pool.submit(
                _execute_function,
                server,
                sandbox_id,
                function,
                run_id,
                async_job=async_job,
            )
            for run_id, async_job in zip(run_ids, async_modes, strict=True)
        ]
        responses = [future.result() for future in futures]

    outputs: list[dict[str, Any]] = []
    for run_id, async_job, response in zip(
        run_ids, async_modes, responses, strict=True
    ):
        assert response.status_code == HTTPStatus.OK, response.text
        payload = response.json()
        if async_job:
            assert payload["status"] == "accepted", payload
            payload = _wait_for_job(server, sandbox_id, run_id)
        assert payload["status"] == "completed", payload
        assert payload["output_data"]["marker"] == run_id
        assert payload["output_data"]["shared_marker"] == "runtime-to-function"
        outputs.append(payload["output_data"])

    # Probe after every API and JOB invocation is terminal. A one-shot health
    # connection racing the twenty-request admission burst tests the host TCP
    # backlog, not whether the services survived the work.
    runtime_health = server.client.request(
        "GET", f"/sandboxes/{sandbox_id}/apps/runtime/health"
    )
    executor_health = server.client.request(
        "GET", f"/sandboxes/{sandbox_id}/apps/function_executor/health"
    )
    assert runtime_health.status_code == HTTPStatus.OK, runtime_health.text
    assert executor_health.status_code == HTTPStatus.OK, executor_health.text

    elapsed = time.monotonic() - started
    assert len(outputs) == FUNCTION_RUN_COUNT
    assert len({item["pid"] for item in outputs}) == FUNCTION_RUN_COUNT
    assert _peak_function_overlap(outputs) == FUNCTION_CONCURRENCY
    # All twenty requests enter an eight-slot executor together. The timestamps
    # prove both real overlap and admission limiting without depending on a
    # transient queued status that a fast runner can legitimately miss.
    # Cold imports on a deliberately constrained 1-vCPU runtime can dominate
    # the first wave, so this bound detects a deadlock without encoding
    # workstation performance.
    assert elapsed < 60, f"mixed API/JOB batch took {elapsed:.2f}s"
    function_to_runtime = _exec(
        server,
        sandbox_id,
        session_id,
        {
            "cmd": "test $(find . -maxdepth 1 -name '*.txt' | wc -l) -eq 21",
            "timeout": 30,
        },
    )
    assert function_to_runtime.status_code == HTTPStatus.OK, function_to_runtime.text
    assert function_to_runtime.json()["success"] is True


def test_real_local_provider_browser_http_and_websocket_proxy(
    real_local_provider_server: _RealProviderServer,
    local_sandbox_id: str,
) -> None:
    websockets = pytest.importorskip("websockets")
    server = real_local_provider_server
    sandbox_id = local_sandbox_id
    _ensure(server, sandbox_id)
    session_id = "browser-proxy"
    _create_session(server, sandbox_id, session_id)

    page = _exec(
        server,
        sandbox_id,
        session_id,
        {
            "cmd": (
                "printf '%s' '<html><body>real provider browser</body></html>' "
                f"> {CONVERSATION_ROOT}/browser-proxy/page.html && "
                f"agent-browser open file://{CONVERSATION_ROOT}/browser-proxy/page.html"
            ),
            "tty": True,
            "yield_time_ms": 1500,
            "timeout": 60,
        },
    )
    assert page.status_code == HTTPStatus.OK, page.text
    assert page.json()["success"] is True

    access = server.client.request(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/browser/access",
        body={"ttl_seconds": 300},
    )
    assert access.status_code == HTTPStatus.OK, access.text
    access_url = access.json()["url"]
    first = server.public_get(access_url)
    assert first.status_code == HTTPStatus.OK, first.text
    cookie = first.headers.get("set-cookie", "")
    assert f"agentbox_app_access_browser_{sandbox_id}" in cookie

    parsed = parse.urlsplit(access_url)
    sessions_url = parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/api/sessions", parsed.query, "")
    )
    sessions = server.public_get(sessions_url, cookie=cookie)
    assert sessions.status_code == HTTPStatus.OK, sessions.text
    browser_sessions = json.loads(sessions.text)
    assert browser_sessions
    browser_port = int(browser_sessions[0]["port"])
    websocket_url = parse.urlunsplit(
        (
            "ws" if parsed.scheme == "http" else "wss",
            parsed.netloc,
            f"/api/session/{browser_port}/stream",
            parsed.query,
            "",
        )
    )

    async def connect() -> None:
        async with websockets.connect(
            websocket_url,
            open_timeout=15,
            close_timeout=2,
        ):
            return

    asyncio.run(connect())


def test_real_local_provider_idle_suspend_resumes_same_filesystem_then_deletes(
    real_local_provider_server: _RealProviderServer,
    local_sandbox_id: str,
) -> None:
    server = real_local_provider_server
    sandbox_id = local_sandbox_id
    _ensure(server, sandbox_id)
    initial_identity = server.provider_identity(sandbox_id)
    session_id = "retained-workspace"
    _create_session(server, sandbox_id, session_id)
    sentinel = _exec(
        server,
        sandbox_id,
        session_id,
        {
            "cmd": "printf retained > retained.txt",
            "timeout": 30,
        },
    )
    assert sentinel.json().get("success") is True, sentinel.text
    deleted_session = server.client.request(
        "DELETE", f"/sandboxes/{sandbox_id}/sessions/{session_id}"
    )
    assert deleted_session.status_code == HTTPStatus.OK, deleted_session.text

    deadline = time.monotonic() + 20
    stopped: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        status = server.client.request("GET", f"/sandboxes/{sandbox_id}")
        assert status.status_code == HTTPStatus.OK, status.text
        stopped = status.json()
        if stopped["status"] == "STOPPED":
            break
        time.sleep(0.5)
    assert stopped == {"id": sandbox_id, "ready": False, "status": "STOPPED"}

    # A provider-native stop must keep both the provider object and workspace.
    assert server.provider_identity(sandbox_id) == initial_identity
    _ensure(server, sandbox_id)
    assert server.provider_identity(sandbox_id) == initial_identity
    _create_session(server, sandbox_id, session_id)
    retained = _exec(
        server,
        sandbox_id,
        session_id,
        {"cmd": "cat retained.txt; printf '\\n'; pwd", "timeout": 30},
    )
    assert retained.status_code == HTTPStatus.OK, retained.text
    retained_lines = retained.json()["stdout"].splitlines()
    assert retained_lines == ["retained", f"{CONVERSATION_ROOT}/{session_id}"]

    deleted = server.client.request("DELETE", f"/sandboxes/{sandbox_id}")
    assert deleted.status_code == HTTPStatus.OK, deleted.text
    assert deleted.json() == {"sandbox_id": sandbox_id, "deleted": True}
    missing = server.client.request("GET", f"/sandboxes/{sandbox_id}")
    assert missing.status_code == HTTPStatus.NOT_FOUND
    inspect = _run_cli(server.provider, "inspect", f"agentbox-{sandbox_id}")
    assert inspect.returncode != 0, "explicit DELETE left provider compute behind"

    # Permanent deletion must also remove the provider-owned workspace. A new
    # sandbox with the same public logical ID must not inherit the old files.
    _ensure(server, sandbox_id)
    _create_session(server, sandbox_id, session_id)
    absent = _exec(
        server,
        sandbox_id,
        session_id,
        {"cmd": "test ! -e retained.txt", "timeout": 30},
    )
    assert absent.status_code == HTTPStatus.OK, absent.text
    assert absent.json()["success"] is True
