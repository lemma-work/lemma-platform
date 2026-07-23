from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import socket
from urllib.parse import urlparse
from uuid import UUID, uuid4

from agentbox_client import AgentBoxApiError, AgentBoxClient, WorkloadKind
from dotenv import dotenv_values
import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from app.modules.test_support import e2e_base
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server,
    function_image,
    workspace_image,
)
from app.modules.test_support.e2e.worker_process import production_worker_process


pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.real_sandbox]

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org


_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_NGROK_URL = re.compile(r"https://[a-z0-9-]+\.(?:ngrok-free\.app|ngrok\.app)")
_BACKEND_ENV = dotenv_values(Path(__file__).resolve().parents[5] / ".env")


@pytest.fixture(scope="function")
def test_app(
    e2e_settings,
    db_manager,
    monkeypatch,
    tmp_path,
) -> Generator:
    """Build the latency harness with the same pooled DB behavior as production.

    The shared correctness-test app deliberately selects ``NullPool`` so tests
    spanning event loops cannot reuse connections. That behavior creates a new
    Postgres connection for every JOB status read and is not a valid latency
    environment. This fixture retains the isolated E2E databases while enabling
    the normal bounded application and datastore pools.
    """

    del e2e_settings, db_manager
    e2e_base._ensure_repo_root_on_path()
    e2e_base._configure_local_datastore_runtime(monkeypatch, tmp_path)
    e2e_base._reset_supertokens_testing_state()
    from app.core.infrastructure.db.session import get_engine
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    # Pool selection happens once, when each lazy engine is constructed. Keep
    # every other testing-mode behavior (notably deterministic test crypto and
    # auth) while using the production engine topology for this latency suite.
    original_environment = settings.environment
    settings.environment = "development"
    try:
        get_engine()
        get_datastore_engine()
    finally:
        settings.environment = original_environment
    from app.app import create_app

    yield create_app()


def _benchmark_environment(name: str) -> str | None:
    configured = os.getenv(name)
    if configured:
        return configured
    value = _BACKEND_ENV.get(name)
    return value if isinstance(value, str) and value else None


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class FunctionBenchmarkRuntime:
    provider: str
    manager_base_url: str
    manager_api_key: str
    gateway_url: str
    tracked_sandboxes: list[tuple[WorkloadKind, UUID]] = field(default_factory=list)


@asynccontextmanager
async def _cloudflared_quick_tunnel(
    backend_url: str,
) -> AsyncIterator[str]:
    configured = os.getenv("FUNCTION_BENCH_PUBLIC_URL")
    if configured:
        yield configured.rstrip("/")
        return
    try:
        process = await asyncio.create_subprocess_exec(
            "cloudflared",
            "tunnel",
            "--url",
            backend_url,
            "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "cloudflared is required for the E2B function benchmark"
        ) from exc
    assert process.stdout is not None
    recent: list[str] = []

    registered: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def consume_output(published: asyncio.Future[str]) -> None:
        while line := await process.stdout.readline():
            text = line.decode(errors="replace")
            recent.append(text)
            if len(recent) > 100:
                del recent[:-100]
            if not published.done() and (match := _TUNNEL_URL.search(text)):
                published.set_result(match.group(0))
            if not registered.done() and "Registered tunnel connection" in text:
                registered.set_result(None)
        if not published.done():
            published.set_exception(
                RuntimeError(
                    "cloudflared exited before publishing a tunnel: "
                    + "".join(recent[-20:])
                )
            )

    loop = asyncio.get_running_loop()
    published: asyncio.Future[str] = loop.create_future()
    output_task = asyncio.create_task(consume_output(published))
    try:
        public_url = await asyncio.wait_for(asyncio.shield(published), timeout=45)
        await asyncio.wait_for(asyncio.shield(registered), timeout=30)
        public_host = urlparse(public_url).hostname
        if public_host is None:
            raise RuntimeError("Cloudflare tunnel URL has no hostname")
        last_health_error = "no response"
        public_ips: tuple[str, ...] = ()
        local_dns_unavailable = False
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(10):
                if process.returncode is not None:
                    raise RuntimeError(
                        "cloudflared exited after publishing its URL: "
                        + "".join(recent[-20:])
                    )
                try:
                    response = await client.get(f"{public_url}/health")
                    last_health_error = (
                        f"HTTP {response.status_code}: {response.text[:500]}"
                    )
                    if response.status_code == 200:
                        break
                except httpx.HTTPError as exc:
                    last_health_error = f"{type(exc).__name__}: {exc}"
                    if not public_ips:
                        public_ips = await _resolve_public_ipv4(client, public_host)
                    local_dns_unavailable = not public_ips and isinstance(
                        exc, httpx.ConnectError
                    )
                    for public_ip in public_ips:
                        status = await _curl_health_with_resolved_ip(
                            public_url, public_host, public_ip
                        )
                        last_health_error = f"resolved {public_ip}: HTTP {status}"
                        if status == 200:
                            break
                    else:
                        await asyncio.sleep(0.5)
                        continue
                    break
                await asyncio.sleep(0.5)
            else:
                if not local_dns_unavailable:
                    raise RuntimeError(
                        "Cloudflare tunnel never reached backend health; "
                        f"last result: {last_health_error}; cloudflared: "
                        + "".join(recent[-20:])
                    )
        yield public_url
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(output_task, return_exceptions=True)


@asynccontextmanager
async def _ngrok_tunnel(backend_url: str) -> AsyncIterator[str]:
    """Publish the benchmark backend through a temporary ngrok endpoint."""

    try:
        process = await asyncio.create_subprocess_exec(
            "ngrok",
            "http",
            backend_url,
            "--log",
            "stdout",
            "--log-format",
            "json",
            "--log-level",
            "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ngrok is required when FUNCTION_BENCH_TUNNEL=ngrok") from exc
    assert process.stdout is not None
    recent: list[str] = []

    async def consume_output(published: asyncio.Future[str]) -> None:
        while line := await process.stdout.readline():
            text = line.decode(errors="replace")
            recent.append(text)
            if len(recent) > 100:
                del recent[:-100]
            if not published.done() and (match := _NGROK_URL.search(text)):
                published.set_result(match.group(0))
        if not published.done():
            published.set_exception(
                RuntimeError(
                    "ngrok exited before publishing a tunnel: "
                    + "".join(recent[-20:])
                )
            )

    loop = asyncio.get_running_loop()
    published: asyncio.Future[str] = loop.create_future()
    output_task = asyncio.create_task(consume_output(published))
    try:
        public_url = await asyncio.wait_for(asyncio.shield(published), timeout=45)
        last_health_error = "no response"
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(20):
                if process.returncode is not None:
                    raise RuntimeError(
                        "ngrok exited after publishing its URL: "
                        + "".join(recent[-20:])
                    )
                try:
                    response = await client.get(f"{public_url}/health")
                    last_health_error = (
                        f"HTTP {response.status_code}: {response.text[:500]}"
                    )
                    if response.status_code == 200:
                        break
                except httpx.HTTPError as exc:
                    last_health_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(
                    "ngrok tunnel never reached backend health; "
                    f"last result: {last_health_error}; ngrok: "
                    + "".join(recent[-20:])
                )
        yield public_url
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(output_task, return_exceptions=True)


def _e2b_tunnel(backend_url: str):
    configured = os.getenv("FUNCTION_BENCH_PUBLIC_URL")
    if configured:
        return _static_url(configured.rstrip("/"))
    tunnel = os.getenv("FUNCTION_BENCH_TUNNEL", "cloudflared").strip().lower()
    if tunnel == "cloudflared":
        return _cloudflared_quick_tunnel(backend_url)
    if tunnel == "ngrok":
        return _ngrok_tunnel(backend_url)
    raise RuntimeError("FUNCTION_BENCH_TUNNEL must be cloudflared or ngrok")


async def _resolve_public_ipv4(
    client: httpx.AsyncClient, hostname: str
) -> tuple[str, ...]:
    try:
        response = await client.get(
            "https://cloudflare-dns.com/dns-query",
            params={"name": hostname, "type": "A"},
            headers={"Accept": "application/dns-json"},
        )
        response.raise_for_status()
        answers = response.json().get("Answer", [])
        return tuple(
            str(answer["data"])
            for answer in answers
            if int(answer.get("type", 0)) == 1 and answer.get("data")
        )
    except httpx.HTTPError, KeyError, TypeError, ValueError:
        return ()


async def _curl_health_with_resolved_ip(
    public_url: str, hostname: str, public_ip: str
) -> int:
    process = await asyncio.create_subprocess_exec(
        "curl",
        "--silent",
        "--show-error",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--connect-timeout",
        "2",
        "--max-time",
        "5",
        "--resolve",
        f"{hostname}:443:{public_ip}",
        f"{public_url}/health",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    try:
        return int(stdout.decode()) if process.returncode == 0 else 0
    except ValueError:
        return 0


def _agentbox_log_tail(log_path: Path, *, lines: int = 80) -> str:
    try:
        return "".join(
            log_path.read_text(errors="replace").splitlines(keepends=True)[-lines:]
        )
    except OSError:
        return "<AgentBox log unavailable>"


@asynccontextmanager
async def _agentbox_service(
    *,
    provider: str,
    manager_url: str,
    api_key: str,
    state_path: Path,
    workspace_image_name: str,
    function_image_name: str,
    gateway_host: str,
    log_path: Path,
) -> AsyncIterator[None]:
    """Run the real AgentBox service boundary in AgentBox's own environment."""

    agentbox_root = Path(__file__).resolve().parents[6] / "agentbox"
    uvicorn_executable = agentbox_root / ".venv" / "bin" / "uvicorn"
    if not uvicorn_executable.is_file():
        raise RuntimeError(
            f"AgentBox environment is missing {uvicorn_executable}; run uv sync in agentbox"
        )
    parsed_manager = urlparse(manager_url)
    if parsed_manager.port is None:
        raise RuntimeError("AgentBox benchmark URL must contain an explicit port")

    environment = {
        **os.environ,
        "AGENTBOX_PROVIDER": provider,
        "AGENTBOX_API_KEY": api_key,
        "AGENTBOX_API_URL": manager_url,
        "AGENTBOX_RUNTIME_CREDENTIAL_KEY": ("function-benchmark-runtime-key-0001"),
        "AGENTBOX_STATE_DB_PATH": str(state_path),
        "AGENTBOX_AUTO_CREATE_SCHEMA": "true",
        "AGENTBOX_WORKSPACE_IMAGE": workspace_image_name,
        "AGENTBOX_FUNCTION_IMAGE": function_image_name,
        "AGENTBOX_ADD_HOST_GATEWAY": "true",
        "AGENTBOX_WORKSPACE_IDLE_SECONDS": "300",
        "AGENTBOX_FUNCTION_IDLE_SECONDS": "300",
        "AGENTBOX_CLEANUP_INTERVAL_SECONDS": "30",
        "AGENTBOX_LOG_LEVEL": "INFO",
    }
    if provider == "e2b":
        # These are the aliases declared by AgentBox Settings. Keep the backend's
        # benchmark-facing names out of the AgentBox process contract.
        environment.update(
            {
                "E2B_API_KEY": _benchmark_environment("E2B_API_KEY") or "",
                "E2B_WORKSPACE_TEMPLATE": (
                    _benchmark_environment("AGENTBOX_E2B_WORKSPACE_TEMPLATE") or ""
                ),
                "E2B_WORKSPACE_TEMPLATE_BUILD_ID": (
                    _benchmark_environment("AGENTBOX_E2B_WORKSPACE_BUILD_ID") or ""
                ),
                "E2B_FUNCTION_TEMPLATE": (
                    _benchmark_environment("AGENTBOX_E2B_FUNCTION_TEMPLATE") or ""
                ),
                "E2B_FUNCTION_TEMPLATE_BUILD_ID": (
                    _benchmark_environment("AGENTBOX_E2B_FUNCTION_BUILD_ID") or ""
                ),
                "AGENTBOX_E2B_SCOPE": f"e2b:function-bench:{uuid4()}",
                "AGENTBOX_E2B_FUNCTION_ALLOW_OUT": gateway_host,
                "AGENTBOX_E2B_REQUEST_TIMEOUT_SECONDS": "60",
            }
        )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_writer:
        process = await asyncio.create_subprocess_exec(
            str(uvicorn_executable),
            "agentbox.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(parsed_manager.port),
            "--log-level",
            "warning",
            "--no-access-log",
            cwd=agentbox_root,
            env=environment,
            stdout=log_writer,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            last_result = "no response"
            async with httpx.AsyncClient(timeout=2) as client:
                for _ in range(300):
                    if process.returncode is not None:
                        raise RuntimeError(
                            f"AgentBox exited with status {process.returncode} during startup"
                        )
                    try:
                        response = await client.get(f"{manager_url}/health/ready")
                        last_result = (
                            f"HTTP {response.status_code}: {response.text[:500]}"
                        )
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError as exc:
                        last_result = f"{type(exc).__name__}: {exc}"
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError(
                        f"AgentBox readiness timed out; last result: {last_result}"
                    )
            yield
        except BaseException as exc:
            log_writer.flush()
            raise RuntimeError(
                f"{exc}\nAgentBox log tail:\n{_agentbox_log_tail(log_path)}"
            ) from exc
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=15)
                except TimeoutError:
                    process.kill()
                    await process.wait()


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Function Benchmark Pod {uuid4()}",
            "slug": f"function-benchmark-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def function_benchmark_runtime(
    request,
    backend_server,
    e2e_settings,
    db_manager,
    tmp_path,
) -> AsyncGenerator[FunctionBenchmarkRuntime, None]:
    del db_manager
    provider = os.getenv("FUNCTION_BENCH_PROVIDER", "docker").strip().lower()
    if provider not in {"docker", "e2b"}:
        pytest.fail("FUNCTION_BENCH_PROVIDER must be docker or e2b")
    if provider == "e2b":
        required = (
            "E2B_API_KEY",
            "AGENTBOX_E2B_WORKSPACE_TEMPLATE",
            "AGENTBOX_E2B_WORKSPACE_BUILD_ID",
            "AGENTBOX_E2B_FUNCTION_TEMPLATE",
            "AGENTBOX_E2B_FUNCTION_BUILD_ID",
        )
        missing = [name for name in required if not _benchmark_environment(name)]
        if missing:
            pytest.fail("E2B benchmark env is missing: " + ", ".join(missing))

    tunnel_context = (
        _e2b_tunnel(backend_server["host_base_url"])
        if provider == "e2b"
        else _static_url(backend_server["docker_base_url"])
    )
    async with tunnel_context as gateway_url:
        gateway_host = urlparse(gateway_url).hostname
        if gateway_host is None:
            raise RuntimeError("benchmark gateway URL has no hostname")
        port = _available_port()
        manager_url = f"http://127.0.0.1:{port}"
        api_key = e2e_settings.agentbox_api_key
        if provider == "docker":
            selected_function_image = request.getfixturevalue("function_image")
            selected_workspace_image = request.getfixturevalue("workspace_image")
        else:
            selected_function_image = "agentbox-function:unused-by-e2b"
            selected_workspace_image = "agentbox-workspace:unused-by-e2b"
        original_backend = {
            "api_url": settings.api_url,
            "agentbox_api_url": settings.agentbox_api_url,
            "agentbox_api_key": settings.agentbox_api_key,
            "function_runtime_gateway_url": settings.function_runtime_gateway_url,
        }
        runtime: FunctionBenchmarkRuntime | None = None
        benchmark_error: BaseException | None = None
        try:
            settings.api_url = gateway_url
            settings.agentbox_api_url = manager_url
            settings.agentbox_api_key = api_key
            settings.function_runtime_gateway_url = gateway_url

            async with _agentbox_service(
                provider=provider,
                manager_url=manager_url,
                api_key=api_key,
                state_path=tmp_path / "agentbox-state.db",
                workspace_image_name=selected_workspace_image,
                function_image_name=selected_function_image,
                gateway_host=gateway_host,
                log_path=tmp_path / f"agentbox-{provider}.log",
            ):
                runtime = FunctionBenchmarkRuntime(
                    provider=provider,
                    manager_base_url=manager_url,
                    manager_api_key=api_key,
                    gateway_url=gateway_url,
                )
                worker_environment = {
                    "AGENTBOX_API_URL": manager_url,
                    "AGENTBOX_API_KEY": api_key,
                    "API_URL": gateway_url,
                    "FUNCTION_RUNTIME_GATEWAY_URL": gateway_url,
                    "DEBUG": "false",
                    "LOG_LEVEL": "INFO",
                }
                try:
                    async with production_worker_process(
                        e2e_settings,
                        log_prefix=f"function_benchmark_{provider}",
                        extra_env=worker_environment,
                        readiness_markers=('"event": "service.started"',),
                    ) as worker:
                        try:
                            yield runtime
                        except BaseException as exc:
                            benchmark_error = exc
                            print(worker.read_log_tail())
                            raise
                finally:
                    cleanup_errors: list[str] = []
                    async with AgentBoxClient(
                        base_url=manager_url,
                        api_key=api_key,
                        timeout_seconds=60,
                    ) as client:
                        for workload_kind, logical_id in runtime.tracked_sandboxes:
                            try:
                                await client.destroy_sandbox(
                                    workload_kind,
                                    logical_id,
                                    deadline_at=(
                                        datetime.now(UTC) + timedelta(seconds=60)
                                    ),
                                )
                            except AgentBoxApiError as exc:
                                if exc.code == "SANDBOX_NOT_FOUND":
                                    continue
                                cleanup_errors.append(
                                    f"{workload_kind.value}/{logical_id}: "
                                    f"{type(exc).__name__}: {exc}"
                                )
                            except Exception as exc:
                                cleanup_errors.append(
                                    f"{workload_kind.value}/{logical_id}: "
                                    f"{type(exc).__name__}: {exc}"
                                )
                    if cleanup_errors:
                        message = (
                            "AgentBox benchmark sandbox cleanup failed:\n"
                            + "\n".join(cleanup_errors)
                        )
                        if benchmark_error is not None:
                            benchmark_error.add_note(message)
                        else:
                            raise RuntimeError(message)
        finally:
            for name, value in original_backend.items():
                setattr(settings, name, value)


@asynccontextmanager
async def _static_url(url: str) -> AsyncIterator[str]:
    yield url.rstrip("/")


__all__ = [
    "authenticated_client",
    "backend_server",
    "db_manager",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "function_benchmark_runtime",
    "function_image",
    "postgres_container",
    "redis_container",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_pod",
    "test_redis_url",
    "workspace_image",
]
