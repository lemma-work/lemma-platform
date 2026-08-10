from __future__ import annotations

from sandbox_runtime.errors import (
    SandboxError,
    SandboxNotFound,
)

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

from sandbox_runtime.protocol import WorkloadKind
from dotenv import dotenv_values
import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from app.modules.workspace.config import workspace_settings
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
            "E2B_WORKSPACE_TEMPLATE",
            "E2B_WORKSPACE_BUILD_ID",
            "E2B_FUNCTION_TEMPLATE",
            "E2B_FUNCTION_BUILD_ID",
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
        if provider == "docker":
            selected_function_image = request.getfixturevalue("function_image")
            selected_workspace_image = request.getfixturevalue("workspace_image")
        else:
            selected_function_image = "lemma-function:unused-by-e2b"
            selected_workspace_image = "lemma-workspace:unused-by-e2b"
        # Two settings objects, restored separately: writing a workspace field
        # back onto the core settings would silently create an attribute there
        # and leave the real one overridden for the rest of the session.
        original_backend = {
            "api_url": settings.api_url,
            "function_runtime_gateway_url": settings.function_runtime_gateway_url,
        }
        original_workspace = {
            "provider": workspace_settings.provider,
            "workspace_image": workspace_settings.workspace_image,
            "function_image": workspace_settings.function_image,
            "docker_allow_mutable_images": (
                workspace_settings.docker_allow_mutable_images
            ),
            "add_host_gateway": workspace_settings.add_host_gateway,
            "host_alias": workspace_settings.host_alias,
        }
        runtime: FunctionBenchmarkRuntime | None = None
        benchmark_error: BaseException | None = None
        try:
            settings.api_url = gateway_url
            settings.function_runtime_gateway_url = gateway_url
            workspace_settings.provider = provider
            workspace_settings.workspace_image = selected_workspace_image
            workspace_settings.function_image = selected_function_image
            if provider == "docker":
                # The benchmark images are content-addressed tags, not digests,
                # and the sandboxes have to reach this process to fetch their
                # artifact -- both are the provisioner's business now that it
                # runs here rather than in a manager process.
                workspace_settings.docker_allow_mutable_images = True
                workspace_settings.add_host_gateway = True
                workspace_settings.host_alias = "host.docker.internal"

            # Provisioning runs in this process, so the measurement covers
            # the same code path production does.
            from app.modules.workspace.services.sandbox_composition import (
                reset_sandbox_service,
            )

            await reset_sandbox_service()
            runtime = FunctionBenchmarkRuntime(
                provider=provider,
                gateway_url=gateway_url,
            )
            worker_environment = {
                "API_URL": gateway_url,
                "FUNCTION_RUNTIME_GATEWAY_URL": gateway_url,
                "WORKSPACE_PROVIDER": provider,
                "WORKSPACE_IMAGE": selected_workspace_image,
                "FUNCTION_IMAGE": selected_function_image,
                "DEBUG": "false",
                "LOG_LEVEL": "INFO",
            }
            if provider == "docker":
                worker_environment.update(
                    {
                        "WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES": "true",
                        "WORKSPACE_ADD_HOST_GATEWAY": "true",
                        "WORKSPACE_HOST_ALIAS": "host.docker.internal",
                    }
                )
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
                from app.modules.workspace.services.sandbox_composition import (
                    build_local_client,
                )

                async with build_local_client() as client:
                    for workload_kind, logical_id in runtime.tracked_sandboxes:
                        try:
                            await client.destroy_sandbox(
                                workload_kind,
                                logical_id,
                                deadline_at=(
                                    datetime.now(UTC) + timedelta(seconds=60)
                                ),
                            )
                        except SandboxNotFound:
                            continue
                        except SandboxError as exc:
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
                        "benchmark sandbox cleanup failed:\n"
                        + "\n".join(cleanup_errors)
                    )
                    if benchmark_error is not None:
                        benchmark_error.add_note(message)
                    else:
                        raise RuntimeError(message)
        finally:
            for name, value in original_backend.items():
                setattr(settings, name, value)
            for name, value in original_workspace.items():
                setattr(workspace_settings, name, value)


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
