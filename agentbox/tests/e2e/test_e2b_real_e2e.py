from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error, parse
from uuid import uuid4

import pytest

from conftest import AgentBoxServer, available_port


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]
E2B_FUNCTION_CONCURRENCY = int(
    os.environ.get("AGENTBOX_E2E_FUNCTION_MAX_CONCURRENCY", "8")
)
if not 1 <= E2B_FUNCTION_CONCURRENCY < 10:
    raise ValueError("E2B E2E function concurrency must be between 1 and 9")


@dataclass
class RealE2BServer(AgentBoxServer):
    provider_owner: str
    provider_environment: str
    manager_log_path: Path

    def diagnostics(self, *secrets: str) -> str:
        try:
            value = self.manager_log_path.read_text(errors="replace")[-12000:]
        except OSError as exc:
            return f"manager log unavailable: {exc}"
        return _redact(
            value,
            os.environ.get("E2B_API_KEY", ""),
            self.api_key,
            *secrets,
        )


def _redact(value: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _provider_id_hint(response: object) -> str | None:
    """Extract only an E2B-native ID from a structured gateway failure."""

    try:
        payload = json.loads(str(getattr(response, "text", "")))
    except json.JSONDecodeError:
        return None
    candidates: list[object] = [payload]
    if isinstance(payload, dict):
        candidates.append(payload.get("detail"))
        detail = payload.get("detail")
        if isinstance(detail, dict):
            candidates.append(detail.get("runtime_body"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("sandboxId", "provider_id"):
            value = candidate.get(key)
            if isinstance(value, str) and re.fullmatch(r"[a-z0-9-]{8,64}", value):
                return value
    return None


def _request_not_delivered(response: object) -> bool:
    headers = getattr(response, "headers", {})
    return str(headers.get("x-agentbox-request-not-delivered", "")).lower() == "true"


def _safe_process_kind(command: object) -> str:
    """Classify a command without exposing arguments or environment values."""

    value = str(command).lower()
    for kind in (
        "start-runtime",
        "function_executor_worker",
        "function_executor",
        "runtime_server",
        "agent-browser",
        "xvfb",
        "socat",
        "python",
        "bash",
        "sh",
    ):
        if kind in value:
            return kind
    return "other"


async def _scoped_e2b_infos(
    *,
    owner: str,
    environment: str,
    logical_id: str | None = None,
) -> list[object]:
    """List only provider objects created by this test manager.

    The API key is intentionally read at the last possible moment and is never
    included in a test ID, fixture repr, subprocess argument, or failure text.
    """

    from e2b import AsyncSandbox, RateLimitException, SandboxQuery

    metadata = {
        "managed-by": "agentbox",
        "agentbox-owner": owner,
        "agentbox-environment": environment,
    }
    if logical_id is not None:
        metadata["agentbox-id"] = logical_id
    for attempt in range(6):
        try:
            paginator = AsyncSandbox.list(
                query=SandboxQuery(metadata=metadata),
                limit=100,
                api_key=os.environ["E2B_API_KEY"],
                request_timeout=5,
            )
            infos: list[object] = []
            while paginator.has_next:
                infos.extend(await paginator.next_items())
            return infos
        except RateLimitException:
            if attempt == 5:
                raise
            await asyncio.sleep(min(2**attempt, 8))
    raise RuntimeError("unreachable")


def _provider_id(server: RealE2BServer, sandbox_id: str) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        infos = asyncio.run(
            _scoped_e2b_infos(
                owner=server.provider_owner,
                environment=server.provider_environment,
                logical_id=sandbox_id,
            )
        )
        if len(infos) == 1:
            return str(getattr(infos[0], "sandbox_id"))
        time.sleep(0.5)
    raise AssertionError(f"Expected one E2B provider object for {sandbox_id}")


def _wait_provider_absent(server: RealE2BServer, sandbox_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        infos = asyncio.run(
            _scoped_e2b_infos(
                owner=server.provider_owner,
                environment=server.provider_environment,
                logical_id=sandbox_id,
            )
        )
        if not infos:
            return
        time.sleep(0.5)
    raise AssertionError(f"E2B provider object still exists for {sandbox_id}")


async def _purge_scoped_e2b(owner: str, environment: str) -> None:
    from e2b import AsyncSandbox, SandboxNotFoundException

    for info in await _scoped_e2b_infos(owner=owner, environment=environment):
        provider_id = str(getattr(info, "sandbox_id"))
        try:
            await AsyncSandbox.kill(provider_id, api_key=os.environ["E2B_API_KEY"])
        except SandboxNotFoundException:
            pass


@pytest.fixture(scope="module")
def real_e2b_server() -> Generator[RealE2BServer, None, None]:
    api_key = os.environ.get("E2B_API_KEY", "").strip()
    template = os.environ.get("E2B_SANDBOX_TEMPLATE", "").strip()
    if not api_key:
        pytest.skip("real E2B e2e requires E2B_API_KEY")
    if not template:
        pytest.skip("real E2B e2e requires E2B_SANDBOX_TEMPLATE")
    pytest.importorskip("e2b")

    repo_root = Path(__file__).resolve().parents[3]
    port = available_port()
    manager_key = f"agentbox-e2b-e2e-{uuid4().hex}"
    base_url = f"http://127.0.0.1:{port}"
    owner = f"platform-pr-e2e-{uuid4().hex[:12]}"
    environment = "real-e2b"
    app_domain = f"127-0-0-1.sslip.io:{port}"

    with TemporaryDirectory(prefix="agentbox-e2b-real-e2e-") as tmpdir:
        env = {
            **os.environ,
            "PYTHONPATH": str(repo_root / "agentbox"),
            "AGENTBOX_PROVIDER": "e2b",
            "AGENTBOX_API_KEY": manager_key,
            "AGENTBOX_API_URL": base_url,
            "AGENTBOX_APP_DOMAIN": app_domain,
            "AGENTBOX_PROVIDER_OWNER": owner,
            "AGENTBOX_ENVIRONMENT": environment,
            "AGENTBOX_STATE_DB_PATH": str(Path(tmpdir) / "state.db"),
            "AGENTBOX_STATE_DURABLE_ENV_KEYS": ("LEMMA_BASE_URL,E2B_E2E_DYNAMIC_MARK"),
            "AGENTBOX_FUNCTION_MAX_CONCURRENCY": str(E2B_FUNCTION_CONCURRENCY),
            "AGENTBOX_FUNCTION_MAX_QUEUED": "32",
            "AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS": "900",
            "AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS": "900",
            "AGENTBOX_CLEANUP_INTERVAL_SECONDS": "30",
            "AGENTBOX_SANDBOX_READY_TIMEOUT_SECONDS": "300",
            "E2B_SANDBOX_MAX_ACTIVE": "4",
            "E2B_SANDBOX_ADMISSION_WAIT_SECONDS": "120",
            "E2B_SANDBOX_CREATE_RATE_PER_SECOND": "1",
            "E2B_SANDBOX_CREATE_MAX_IN_FLIGHT": "1",
            "E2B_SANDBOX_TIMEOUT_SECONDS": "3600",
        }
        manager_log_path = Path(tmpdir) / "manager.log"
        log_fd = os.open(
            manager_log_path,
            os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
            0o600,
        )
        try:
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
                    "info",
                    "--no-access-log",
                ],
                cwd=repo_root,
                env=env,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                text=True,
            )
        finally:
            os.close(log_fd)
        server = RealE2BServer(
            base_url=base_url,
            api_key=manager_key,
            app_domain=app_domain,
            provider_owner=owner,
            provider_environment=environment,
            manager_log_path=manager_log_path,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(
                        "Real E2B manager exited during startup.\n"
                        f"LOG:\n{server.diagnostics()}"
                    )
                try:
                    health = server.anonymous_client.request_json(
                        "GET", "/health", timeout=2
                    )
                    if health.status_code == HTTPStatus.OK:
                        break
                except error.URLError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("Timed out starting the real E2B manager")
            yield server
        finally:
            # The manager path is the normal cleanup path. The provider-scoped
            # sweep is a failure-safe for a test interrupted between create and
            # recording its logical ID; the owner is random per module run.
            try:
                asyncio.run(_purge_scoped_e2b(owner, environment))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


@pytest.fixture
def real_e2b_sandbox_id(
    request: pytest.FixtureRequest,
    real_e2b_server: RealE2BServer,
) -> Generator[str, None, None]:
    name = request.node.name.lower().replace("_", "-")[:32].strip("-")
    sandbox_id = f"re2b-{name}-{uuid4().hex[:8]}"[:63].rstrip("-")
    yield sandbox_id
    real_e2b_server.cleanup_sandbox(sandbox_id)


def _ensure(
    server: RealE2BServer,
    sandbox_id: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    response = None
    for attempt in range(1, 5):
        response = server.client.request_json(
            "PUT",
            f"/sandboxes/{sandbox_id}",
            body={"env": env or {}},
            timeout=360,
        )
        if response.status_code == HTTPStatus.OK:
            break
        try:
            detail = response.json().get("detail", {})
        except (AttributeError, json.JSONDecodeError):
            detail = {}
        if (
            response.status_code
            not in {
                HTTPStatus.SERVICE_UNAVAILABLE,
                HTTPStatus.GATEWAY_TIMEOUT,
            }
            or not isinstance(detail, dict)
            or detail.get("retryable") is not True
            or attempt == 4
        ):
            break
        time.sleep(float(response.headers.get("retry-after", "1")))
    assert response is not None
    assert response.status_code == HTTPStatus.OK, (
        f"{response.text}\nManager log:\n{server.diagnostics()}"
    )
    sandbox = response.json()["sandbox"]
    assert sandbox == {"id": sandbox_id, "ready": True, "status": "RUNNING"}
    return sandbox


def _create_session(
    server: RealE2BServer,
    sandbox_id: str,
    session_id: str,
    *,
    cwd: str,
) -> None:
    response = None
    for attempt in range(1, 5):
        response = server.client.request_json(
            "PUT",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}",
            body={"env": {"E2B_SESSION_MARK": session_id}, "cwd": cwd},
            timeout=180,
        )
        if response.status_code == HTTPStatus.OK:
            break
        try:
            detail = response.json().get("detail", {})
        except (AttributeError, json.JSONDecodeError):
            detail = {}
        if (
            response.status_code != HTTPStatus.SERVICE_UNAVAILABLE
            or not isinstance(detail, dict)
            or detail.get("code")
            not in {
                "endpoint_routing_unavailable",
                "runtime_routing_unavailable",
            }
            or attempt == 4
        ):
            break
        time.sleep(float(response.headers.get("retry-after", "1")))
    assert response is not None
    provider_diagnostics = ""
    if response.status_code != HTTPStatus.OK:
        provider_diagnostics = asyncio.run(
            _e2b_provider_diagnostics(
                server,
                sandbox_id,
                provider_id_hint=_provider_id_hint(response),
            )
        )
    assert response.status_code == HTTPStatus.OK, (
        f"{response.text}\nProvider diagnostics: {provider_diagnostics}"
        f"\nManager log:\n{server.diagnostics()}"
    )
    assert response.json()["cwd"] == cwd


def _exec(
    server: RealE2BServer,
    sandbox_id: str,
    session_id: str,
    cmd: str,
    *,
    timeout: int = 60,
    yield_time_ms: int | None = None,
    tty: bool = False,
) -> dict[str, object]:
    body: dict[str, object] = {
        "cmd": cmd,
        "timeout": timeout,
        "tty": tty,
        "max_output_tokens": 20000,
    }
    if yield_time_ms is not None:
        body["yield_time_ms"] = yield_time_ms
    response = server.client.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body=body,
        timeout=timeout + 30,
    )
    provider_diagnostics = ""
    if response.status_code != HTTPStatus.OK:
        provider_diagnostics = asyncio.run(
            _e2b_provider_diagnostics(
                server,
                sandbox_id,
                provider_id_hint=_provider_id_hint(response),
            )
        )
    assert response.status_code == HTTPStatus.OK, (
        f"{response.text}\nProvider diagnostics: {provider_diagnostics}"
        f"\nManager log:\n{server.diagnostics()}"
    )
    return response.json()


async def _e2b_provider_diagnostics(
    server: RealE2BServer,
    sandbox_id: str,
    *,
    secrets: tuple[str, ...] = (),
    provider_id_hint: str | None = None,
) -> str:
    """Collect bounded, credential-free control/data-plane evidence.

    A function-executor gateway failure can mean either provider propagation,
    CPU starvation, or the executor process being killed. Capture all three
    signals before fixture cleanup, but never include process environments,
    command arguments, request headers, or unbounded logs.
    """

    from e2b import AsyncSandbox

    async def collect() -> str:
        infos = await _scoped_e2b_infos(
            owner=server.provider_owner,
            environment=server.provider_environment,
            logical_id=sandbox_id,
        )
        if not infos and provider_id_hint is None:
            return "inventory=absent"
        provider_id = provider_id_hint or str(getattr(infos[0], "sandbox_id"))
        matching_info = next(
            (
                info
                for info in infos
                if str(getattr(info, "sandbox_id", "")) == provider_id
            ),
            None,
        )
        state = (
            str(getattr(matching_info, "state", "unknown"))
            if matching_info is not None
            else "inventory-absent-provider-hint"
        )
        try:
            sandbox = await asyncio.wait_for(
                AsyncSandbox.connect(
                    provider_id,
                    timeout=60,
                    api_key=os.environ["E2B_API_KEY"],
                    request_timeout=5,
                ),
                timeout=5,
            )
            source = _sandbox_resource_diagnostic_source()
            encoded_source = base64.b64encode(source.encode()).decode()
            running_result, processes_result, resource_result = await asyncio.gather(
                asyncio.wait_for(sandbox.is_running(request_timeout=5), timeout=5),
                asyncio.wait_for(sandbox.commands.list(request_timeout=5), timeout=5),
                asyncio.wait_for(
                    sandbox.commands.run(
                        "python -c \"import base64;exec(base64.b64decode('"
                        f"{encoded_source}'))\"",
                        user="appuser",
                        timeout=8,
                        request_timeout=5,
                    ),
                    timeout=8,
                ),
                return_exceptions=True,
            )
            # Only PID and executable basename are safe to report. E2B process
            # objects also contain full arguments and environments, which may
            # contain delegated tokens or application credentials.
            if isinstance(processes_result, BaseException):
                process_summary: object = {"error": type(processes_result).__name__}
            else:
                process_summary = [
                    {
                        "pid": int(getattr(process, "pid", -1)),
                        "command": _safe_process_kind(
                            getattr(process, "cmd", "unknown")
                        ),
                    }
                    for process in processes_result[:32]
                ]
            if isinstance(resource_result, BaseException):
                resource_exit: object = None
                resource_output = json.dumps({"error": type(resource_result).__name__})
            else:
                resource_exit = getattr(resource_result, "exit_code", None)
                resource_output = str(getattr(resource_result, "stdout", ""))[-20000:]
            diagnostic = json.dumps(
                {
                    "inventory_state": state,
                    "control_running": (
                        {"error": type(running_result).__name__}
                        if isinstance(running_result, BaseException)
                        else bool(running_result)
                    ),
                    "command_processes": process_summary,
                    "resource_probe_exit": resource_exit,
                    "resource_probe": resource_output,
                },
                separators=(",", ":"),
            )
            return _redact(
                diagnostic,
                os.environ.get("E2B_API_KEY", ""),
                server.api_key,
                *secrets,
            )
        except Exception as exc:  # noqa: BLE001 - preserve original failure
            return f"inventory_state={state} diagnostic_error={type(exc).__name__}"

    try:
        return await asyncio.wait_for(collect(), timeout=20)
    except TimeoutError:
        return "diagnostic_error=TimeoutError"
    except Exception as exc:  # noqa: BLE001 - diagnostic must not mask original
        return f"diagnostic_error={type(exc).__name__}"


def _sandbox_resource_diagnostic_source() -> str:
    """Return a dependency-free probe that is safe to execute in a sandbox."""

    return r"""
import json
import os
import urllib.request


def read_text(path, maximum=12000):
    try:
        with open(path, "rb") as handle:
            return handle.read(maximum).decode("utf-8", errors="replace")
    except OSError as exc:
        return "unavailable:" + type(exc).__name__


def read_tail(path, maximum=12000):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - maximum))
            return handle.read(maximum).decode("utf-8", errors="replace")
    except OSError as exc:
        return "unavailable:" + type(exc).__name__


def health(port):
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % port, timeout=2
        ) as response:
            return {"status": response.status}
    except Exception as exc:
        return {"error": type(exc).__name__}


memory_files = [
    "/sys/fs/cgroup/memory.current",
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory.peak",
    "/sys/fs/cgroup/memory.events",
    "/sys/fs/cgroup/memory.events.local",
    "/sys/fs/cgroup/memory.swap.current",
    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    "/sys/fs/cgroup/memory/memory.max_usage_in_bytes",
    "/sys/fs/cgroup/memory/memory.failcnt",
]
memory = {}
for path in memory_files:
    if os.path.exists(path):
        key = path.removeprefix("/sys/fs/cgroup/").replace("/", ".")
        memory[key] = read_text(path, 2048).strip()

processes = []
page_kib = os.sysconf("SC_PAGE_SIZE") // 1024
known_processes = (
    "python", "bash", "sh", "socat", "xvfb", "node", "chromium", "chrome"
)
for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    try:
        statm = read_text("/proc/" + entry + "/statm", 1024).split()
        raw_command = read_text("/proc/" + entry + "/comm", 256).strip().lower()
        command = next(
            (name for name in known_processes if name in raw_command), "other"
        )
        processes.append(
            {
                "pid": int(entry),
                "command": command,
                "rss_kib": int(statm[1]) * page_kib,
            }
        )
    except (IndexError, OSError, ValueError):
        continue
processes.sort(key=lambda item: item["rss_kib"], reverse=True)

print(
    json.dumps(
        {
            "health": {"runtime_8080": health(8080), "executor_8090": health(8090)},
            "memory": memory,
            "processes_by_rss": processes[:24],
            "function_executor_log_tail": read_tail(
                "/tmp/agentbox-function-executor.log"
            ),
        },
        separators=(",", ":"),
    )
)
""".strip()


def _assert_function_response_ok(
    server: RealE2BServer,
    sandbox_id: str,
    response: object,
    *,
    token: str,
) -> None:
    if getattr(response, "status_code", None) == HTTPStatus.OK:
        return
    provider_diagnostics = asyncio.run(
        _e2b_provider_diagnostics(
            server,
            sandbox_id,
            secrets=(token,),
            provider_id_hint=_provider_id_hint(response),
        )
    )
    response_text = _redact(
        str(getattr(response, "text", "")),
        os.environ.get("E2B_API_KEY", ""),
        server.api_key,
        token,
    )
    pytest.fail(
        f"Function executor response failed: {response_text}"
        f"\nProvider diagnostics: {provider_diagnostics}"
        f"\nManager log:\n{server.diagnostics(token)}"
    )


def _assert_websocket_accepts(url: str) -> None:
    websockets = pytest.importorskip("websockets")

    async def connect_once() -> None:
        async with websockets.connect(url, open_timeout=20, close_timeout=2):
            return

    asyncio.run(connect_once())


def test_real_e2b_lifecycle_runtime_pause_resume_and_delete(
    real_e2b_server: RealE2BServer,
    real_e2b_sandbox_id: str,
) -> None:
    marker = f"dynamic-{uuid4().hex}"
    durable_env = {
        "E2B_E2E_DYNAMIC_MARK": marker,
        "LEMMA_BASE_URL": "https://api.example.invalid",
    }
    _ensure(real_e2b_server, real_e2b_sandbox_id, env=durable_env)

    fetched = real_e2b_server.client.request_json(
        "GET", f"/sandboxes/{real_e2b_sandbox_id}", timeout=60
    )
    assert fetched.status_code == HTTPStatus.OK, (
        f"{fetched.text}\nManager log:\n{real_e2b_server.diagnostics()}"
    )
    assert fetched.json() == {
        "id": real_e2b_sandbox_id,
        "ready": True,
        "status": "RUNNING",
    }

    session_id = "runtime-contract"
    cwd = "/workspace/c/2026-07-15/real-e2b-contract"
    _create_session(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        cwd=cwd,
    )

    environment = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "printf '%s|%s|%s' \"$E2B_E2E_DYNAMIC_MARK\" "
        '"$E2B_SESSION_MARK" "$LEMMA_BASE_URL"',
    )
    assert environment["success"] is True
    assert environment["stdout"] == (
        f"{marker}|{session_id}|https://api.example.invalid"
    )

    sentinel = f"sentinel-{uuid4().hex}"
    written = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        f"mkdir -p {shlex.quote(cwd)} && "
        f"printf %s {shlex.quote(sentinel)} > {shlex.quote(cwd + '/sentinel.txt')}",
    )
    assert written["success"] is True

    first_python = real_e2b_server.client.request_json(
        "POST",
        f"/sandboxes/{real_e2b_sandbox_id}/sessions/{session_id}/python",
        body={"code": "value = 40\nvalue + 1", "timeout_seconds": 30},
        timeout=60,
    )
    assert first_python.status_code == HTTPStatus.OK, first_python.text
    assert first_python.json()["result"] == "41"
    second_python = real_e2b_server.client.request_json(
        "POST",
        f"/sandboxes/{real_e2b_sandbox_id}/sessions/{session_id}/python",
        body={"code": "value += 1\nvalue", "timeout_seconds": 30},
        timeout=60,
    )
    assert second_python.status_code == HTTPStatus.OK, second_python.text
    assert second_python.json()["result"] == "41"

    interactive = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "read line; printf 'stdin:%s\\n' \"$line\"",
        timeout=45,
        yield_time_ms=300,
    )
    assert interactive["completed"] is False
    process_id = str(interactive["process_id"])
    stdin = real_e2b_server.client.request_json(
        "POST",
        f"/sandboxes/{real_e2b_sandbox_id}/sessions/{session_id}/stdin",
        body={
            "process_id": process_id,
            "chars": "hello-e2b\n",
            "yield_time_ms": 1500,
        },
        timeout=60,
    )
    assert stdin.status_code == HTTPStatus.OK, stdin.text
    assert stdin.json()["completed"] is True
    assert "stdin:hello-e2b" in stdin.json()["stdout"]

    tty = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "python -c 'import sys; print(sys.stdin.isatty(), sys.stdout.isatty())'",
        tty=True,
        yield_time_ms=1500,
    )
    assert tty["success"] is True
    assert "True True" in str(tty["stdout"])

    sleeper = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "sleep 60",
        timeout=90,
        yield_time_ms=300,
    )
    assert sleeper["completed"] is False
    sleeper_id = str(sleeper["process_id"])
    terminated = real_e2b_server.client.request_json(
        "DELETE",
        f"/sandboxes/{real_e2b_sandbox_id}/sessions/{session_id}/processes/"
        f"{sleeper_id}",
        timeout=60,
    )
    assert terminated.status_code == HTTPStatus.OK, terminated.text
    assert terminated.json()["completed"] is True

    provider_id = _provider_id(real_e2b_server, real_e2b_sandbox_id)
    suspended = real_e2b_server.client.request_json(
        "POST", f"/sandboxes/{real_e2b_sandbox_id}/suspend", timeout=180
    )
    assert suspended.status_code == HTTPStatus.OK, suspended.text
    assert suspended.json()["suspended"] is True
    assert _provider_id(real_e2b_server, real_e2b_sandbox_id) == provider_id

    stopped = real_e2b_server.client.request_json(
        "GET", f"/sandboxes/{real_e2b_sandbox_id}", timeout=60
    )
    assert stopped.status_code == HTTPStatus.OK, stopped.text
    assert stopped.json()["ready"] is False
    assert stopped.json()["status"] == "STOPPED"

    _ensure(real_e2b_server, real_e2b_sandbox_id, env=durable_env)
    assert _provider_id(real_e2b_server, real_e2b_sandbox_id) == provider_id
    resumed_session = "runtime-after-resume"
    _create_session(
        real_e2b_server,
        real_e2b_sandbox_id,
        resumed_session,
        cwd=cwd,
    )
    restored = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        resumed_session,
        "cat sentinel.txt",
    )
    assert restored["stdout"] == sentinel

    deleted = real_e2b_server.client.request_json(
        "DELETE", f"/sandboxes/{real_e2b_sandbox_id}", timeout=180
    )
    assert deleted.status_code == HTTPStatus.OK, deleted.text
    assert deleted.json()["deleted"] is True
    _wait_provider_absent(real_e2b_server, real_e2b_sandbox_id)

    # DELETE is intentionally different from idle suspension: recreating the
    # same logical user gets a new provider generation and an empty filesystem.
    _ensure(real_e2b_server, real_e2b_sandbox_id, env=durable_env)
    assert _provider_id(real_e2b_server, real_e2b_sandbox_id) != provider_id
    fresh_session = "runtime-after-permanent-delete"
    _create_session(
        real_e2b_server,
        real_e2b_sandbox_id,
        fresh_session,
        cwd=cwd,
    )
    absent = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        fresh_session,
        "test ! -e sentinel.txt",
    )
    assert absent["success"] is True


def test_real_e2b_browser_http_and_websocket_proxy(
    real_e2b_server: RealE2BServer,
    real_e2b_sandbox_id: str,
) -> None:
    _ensure(real_e2b_server, real_e2b_sandbox_id)
    session_id = "browser-contract"
    _create_session(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        cwd="/workspace/c/browser-contract",
    )
    browser_available = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "command -v agent-browser",
    )
    if not browser_available["success"]:
        pytest.skip("configured E2B template does not include agent-browser")

    opened = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "agent-browser open https://example.com",
        timeout=90,
        yield_time_ms=2000,
        tty=True,
    )
    assert opened["success"] is True

    access = real_e2b_server.client.request_json(
        "POST",
        f"/sandboxes/{real_e2b_sandbox_id}/apps/browser/access",
        body={"ttl_seconds": 600},
        timeout=60,
    )
    assert access.status_code == HTTPStatus.OK, access.text
    public_url = str(access.json()["url"])
    first = real_e2b_server.public_get(public_url)
    assert first.status_code == HTTPStatus.OK, first.text
    cookie = first.headers.get("set-cookie", "")
    assert f"agentbox_app_access_browser_{real_e2b_sandbox_id}" in cookie

    parsed = parse.urlsplit(public_url)
    sessions_url = parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/api/sessions", parsed.query, "")
    )
    sessions = real_e2b_server.public_get(sessions_url, cookie=cookie)
    assert sessions.status_code == HTTPStatus.OK, sessions.text
    browser_sessions = json.loads(sessions.text)
    assert browser_sessions
    browser_port = int(browser_sessions[0]["port"])
    websocket_url = parse.urlunsplit(
        (
            "ws",
            parsed.netloc,
            f"/api/session/{browser_port}/stream",
            parsed.query,
            "",
        )
    )
    _assert_websocket_accepts(websocket_url)


def _fake_lemma_server_source(
    *,
    token: str,
    pod_id: str,
    function_id: str,
    function_name: str,
) -> str:
    function_code = f"""#input_type_name: ConcurrencyInput
#output_type_name: ConcurrencyOutput
#function_name: {function_name}

import asyncio
import os
import time
from pydantic import BaseModel

class ConcurrencyInput(BaseModel):
    label: str
    marker: str
    workspace_path: str
    expected_sentinel: str

class ConcurrencyOutput(BaseModel):
    label: str
    marker: str
    pid: int
    started_ns: int
    finished_ns: int
    workspace_sentinel: str

async def {function_name}(ctx, data: ConcurrencyInput) -> ConcurrencyOutput:
    started_ns = time.monotonic_ns()
    print(f"start:{{data.label}}:{{data.marker}}")
    await asyncio.sleep(15)
    finished_ns = time.monotonic_ns()
    print(f"finish:{{data.label}}:{{data.marker}}")
    return ConcurrencyOutput(
        label=data.label,
        marker=data.marker,
        pid=os.getpid(),
        started_ns=started_ns,
        finished_ns=finished_ns,
        workspace_sentinel=open(data.workspace_path).read(),
    )
"""
    function_payload = {
        "id": function_id,
        "name": function_name,
        "pod_id": pod_id,
        "type": "API",
        "code": function_code,
    }
    verified_payload = {
        "user_id": str(uuid4()),
        "email": "real-e2b@example.invalid",
        "pod_id": pod_id,
        "organization_id": str(uuid4()),
        "function_id": function_id,
        "function_name": function_name,
        "scopes": ["function:execute"],
    }
    return f"""from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlsplit

TOKEN = {token!r}
FUNCTION_NAME = {function_name!r}
FUNCTION = json.loads({json.dumps(json.dumps(function_payload))})
VERIFIED = json.loads({json.dumps(json.dumps(verified_payload))})

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.send_json(200, {{"ok": True}})
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.send_json(401, {{"detail": "bad token"}})
        if path == "/auth/verify-token":
            return self.send_json(200, VERIFIED)
        if path == "/pods/{pod_id}/functions/" + FUNCTION_NAME:
            return self.send_json(200, FUNCTION)
        return self.send_json(404, {{"detail": "not found", "path": unquote(path)}})

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

ThreadingHTTPServer(("127.0.0.1", 8079), Handler).serve_forever()
"""


def _function_execute(
    server: RealE2BServer,
    sandbox_id: str,
    *,
    pod_id: str,
    function_name: str,
    token: str,
    run_id: str,
    label: str,
    marker: str,
    workspace_path: str,
    expected_sentinel: str,
    async_job: bool,
) -> object:
    response = None
    delay = 0.25
    max_attempts = 12
    for attempt in range(1, max_attempts + 1):
        response = server.client.request_json(
            "POST",
            f"/sandboxes/{sandbox_id}/apps/function_executor/pods/{pod_id}/"
            f"functions/{function_name}/execute",
            body={
                "run_id": run_id,
                "input_data": {
                    "label": label,
                    "marker": marker,
                    "workspace_path": workspace_path,
                    "expected_sentinel": expected_sentinel,
                },
                "async_job": async_job,
                "timeout_seconds": 120,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=180,
        )
        if response.status_code == HTTPStatus.OK:
            break
        # The function executor deduplicates both API and JOB submissions by
        # run_id. Replaying this exact body against the same durable E2B
        # generation is safe when the provider route temporarily reports 5xx;
        # it joins or returns the original invocation rather than executing it
        # again.
        if (
            response.status_code not in {500, 502, 503, 504}
            and not _request_not_delivered(response)
        ) or attempt == max_attempts:
            break
        time.sleep(max(delay, float(response.headers.get("retry-after", "0"))))
        delay = min(delay * 1.5, 2.0)
    assert response is not None
    return response


def _job_status(
    server: RealE2BServer,
    sandbox_id: str,
    run_id: str,
    *,
    token: str,
) -> dict[str, object]:
    response = None
    delay = 0.25
    for attempt in range(1, 13):
        response = server.client.request_json(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}",
            timeout=60,
        )
        if response.status_code == HTTPStatus.OK:
            break
        if response.status_code not in {500, 502, 503, 504} or attempt == 12:
            break
        time.sleep(max(delay, float(response.headers.get("retry-after", "0"))))
        delay = min(delay * 1.5, 2.0)
    assert response is not None
    _assert_function_response_ok(
        server,
        sandbox_id,
        response,
        token=token,
    )
    return response.json()


def _wait_job(
    server: RealE2BServer,
    sandbox_id: str,
    run_id: str,
    *,
    token: str,
    timeout: float = 120,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _job_status(
            server,
            sandbox_id,
            run_id,
            token=token,
        )
        if status["status"] in {"completed", "failed", "cancelled", "timeout"}:
            return status
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for function run {run_id}")


def _job_logs(
    server: RealE2BServer,
    sandbox_id: str,
    run_id: str,
    *,
    token: str,
) -> list[dict[str, object]]:
    response = None
    delay = 0.25
    for attempt in range(1, 13):
        response = server.client.request_json(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/"
            f"runs/{run_id}/logs",
            timeout=60,
        )
        if response.status_code == HTTPStatus.OK:
            break
        if response.status_code not in {500, 502, 503, 504} or attempt == 12:
            break
        time.sleep(max(delay, float(response.headers.get("retry-after", "0"))))
        delay = min(delay * 1.5, 2.0)
    assert response is not None
    _assert_function_response_ok(
        server,
        sandbox_id,
        response,
        token=token,
    )
    return response.json()["logs"]


def _peak_overlap(outputs: list[dict[str, object]]) -> int:
    events: list[tuple[int, int]] = []
    for output in outputs:
        events.append((int(output["started_ns"]), 1))
        events.append((int(output["finished_ns"]), -1))
    active = 0
    peak = 0
    # End events sort before start events at the same timestamp.
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        active += delta
        peak = max(peak, active)
    return peak


def test_real_e2b_twenty_short_function_requests_use_slots_without_leakage(
    real_e2b_server: RealE2BServer,
    real_e2b_sandbox_id: str,
) -> None:
    token = f"test-token-{uuid4().hex}"
    pod_id = str(uuid4())
    function_id = str(uuid4())
    function_name = f"real_e2b_concurrency_{uuid4().hex[:8]}"
    _ensure(
        real_e2b_server,
        real_e2b_sandbox_id,
        env={"LEMMA_BASE_URL": "http://127.0.0.1:8079"},
    )
    session_id = "fake-lemma-api"
    _create_session(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        cwd="/workspace",
    )
    workspace_root = "/workspace/c/2026-07-15/e2b-function-e2e"
    workspace_path = f"{workspace_root}/sentinel.txt"
    workspace_sentinel = f"function-sentinel-{uuid4().hex}"
    project = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        f"mkdir -p {shlex.quote(workspace_root + '/src')} && "
        f"printf %s {shlex.quote(workspace_sentinel)} > "
        f"{shlex.quote(workspace_path)} && "
        f"printf %s {shlex.quote("export const provider = 'e2b';")} > "
        f"{shlex.quote(workspace_root + '/src/provider.ts')}",
    )
    assert project["success"] is True
    source = _fake_lemma_server_source(
        token=token,
        pod_id=pod_id,
        function_id=function_id,
        function_name=function_name,
    )
    encoded_source = base64.b64encode(source.encode()).decode()
    written = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        f"printf %s {shlex.quote(encoded_source)} | base64 -d > /tmp/fake-lemma-api.py",
    )
    assert written["success"] is True
    fake_server = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        "python /tmp/fake-lemma-api.py",
        timeout=180,
        yield_time_ms=300,
    )
    assert fake_server["completed"] is False

    health = _exec(
        real_e2b_server,
        real_e2b_sandbox_id,
        session_id,
        'python -c "import urllib.request; '
        "print(urllib.request.urlopen('http://127.0.0.1:8079/health').status)\"",
    )
    assert health["stdout"].strip() == "200"

    runs = [
        {
            "run_id": str(uuid4()),
            "label": f"run-{index:02d}",
            "marker": uuid4().hex,
            "workspace_path": workspace_path,
            "expected_sentinel": workspace_sentinel,
            # Both public API and background JOB functions now use the
            # executor's short accepted-run transport. Lemma preserves API's
            # synchronous contract by polling these idempotent run-ID routes.
            "async_job": True,
        }
        for index in range(20)
    ]

    # Fill every configured slot and prove they are simultaneously running
    # before submitting the remaining short accepted-run calls.
    for run in runs[:E2B_FUNCTION_CONCURRENCY]:
        response = _function_execute(
            real_e2b_server,
            real_e2b_sandbox_id,
            pod_id=pod_id,
            function_name=function_name,
            token=token,
            **run,
        )
        _assert_function_response_ok(
            real_e2b_server,
            real_e2b_sandbox_id,
            response,
            token=token,
        )
        assert response.json()["status"] == "accepted"

    deadline = time.monotonic() + 30
    observed_statuses: list[str] = []
    while time.monotonic() < deadline:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=E2B_FUNCTION_CONCURRENCY
        ) as status_pool:
            status_futures = [
                status_pool.submit(
                    _job_status,
                    real_e2b_server,
                    real_e2b_sandbox_id,
                    run["run_id"],
                    token=token,
                )
                for run in runs[:E2B_FUNCTION_CONCURRENCY]
            ]
            observed_statuses = [
                str(future.result(timeout=30)["status"]) for future in status_futures
            ]
        if observed_statuses == ["running"] * E2B_FUNCTION_CONCURRENCY:
            break
        time.sleep(0.2)
    else:
        pytest.fail(
            f"{E2B_FUNCTION_CONCURRENCY} E2B function executor slots did not "
            f"overlap; last statuses={observed_statuses}\n"
            f"Manager log:\n{real_e2b_server.diagnostics()}"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        queued_job_futures = [
            pool.submit(
                _function_execute,
                real_e2b_server,
                real_e2b_sandbox_id,
                pod_id=pod_id,
                function_name=function_name,
                token=token,
                **run,
            )
            for run in runs[E2B_FUNCTION_CONCURRENCY:10]
        ]
        queued_job_responses = [future.result() for future in queued_job_futures]
        for response in queued_job_responses:
            _assert_function_response_ok(
                real_e2b_server,
                real_e2b_sandbox_id,
                response,
                token=token,
            )
            assert response.json()["status"] == "accepted"

        queued_statuses = [
            _job_status(
                real_e2b_server,
                real_e2b_sandbox_id,
                run["run_id"],
                token=token,
            )["status"]
            for run in runs[E2B_FUNCTION_CONCURRENCY:10]
        ]
        # Provider route warm-up can consume enough of the 15-second body
        # sleep for the first wave to finish before this observation. The
        # timestamp overlap assertion below remains the authoritative proof
        # that the runtime never exceeded its configured slots.
        assert set(queued_statuses) <= {"queued", "running"}

        api_admission_futures = [
            pool.submit(
                _function_execute,
                real_e2b_server,
                real_e2b_sandbox_id,
                pod_id=pod_id,
                function_name=function_name,
                token=token,
                **run,
            )
            for run in runs[10:]
        ]
        api_admission_responses = [
            future.result(timeout=30) for future in api_admission_futures
        ]

    completed: dict[str, tuple[dict[str, object], list[dict[str, object]]]] = {}
    for response in api_admission_responses:
        _assert_function_response_ok(
            real_e2b_server,
            real_e2b_sandbox_id,
            response,
            token=token,
        )
        assert response.json()["status"] == "accepted"

    for run in runs:
        status = _wait_job(
            real_e2b_server,
            real_e2b_sandbox_id,
            run["run_id"],
            token=token,
        )
        assert status["status"] == "completed", status
        logs = _job_logs(
            real_e2b_server,
            real_e2b_sandbox_id,
            run["run_id"],
            token=token,
        )
        completed[run["run_id"]] = (
            status["output_data"],
            logs,
        )

    all_markers = {str(run["marker"]) for run in runs}
    outputs: list[dict[str, object]] = []
    for run in runs:
        output, logs = completed[run["run_id"]]
        outputs.append(output)
        assert output["label"] == run["label"]
        assert output["marker"] == run["marker"]
        assert output["workspace_sentinel"] == workspace_sentinel
        combined_logs = "\n".join(str(entry["message"]) for entry in logs)
        assert str(run["marker"]) in combined_logs
        leaked = {
            other
            for other in all_markers - {str(run["marker"])}
            if other in combined_logs
        }
        assert not leaked, f"cross-run markers leaked into {run['run_id']}: {leaked}"

    assert len(completed) == 20
    assert _peak_overlap(outputs) == E2B_FUNCTION_CONCURRENCY
