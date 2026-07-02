"""Shared E2E runtime fixtures for worker, workspace, and scheduler tests."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
import httpx
import uvicorn

from app.core.config import settings


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_root_env_value(key: str) -> str | None:
    """Read a single key from the monorepo root .env (creds are not auto-loaded
    into the sealed e2e settings)."""
    root = Path(__file__).resolve().parents[5]
    env_file = root / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


# Source paths the runtime image is built from (Dockerfile.runtime COPYs these).
# A change to any of them must invalidate the cached image — see
# _agentbox_image_fingerprint.
_AGENTBOX_BUILD_INPUTS = (
    "agentbox",
    "lemma-python",
    "lemma-pod-bundle",
    "lemma-cli",
    "lemma-typescript",
    "lemma-skills",
)


def _agentbox_image_fingerprint(repo_root: Path) -> str | None:
    """Short content hash of the agentbox runtime image's build inputs.

    Combines the committed git tree/blob hashes of the relevant paths with the
    current uncommitted diff and any untracked files, so the fingerprint changes
    exactly when the image would build differently — committed or not. Returns
    None when git can't be used (then the caller falls back to always building).
    """
    paths = list(_AGENTBOX_BUILD_INPUTS)
    try:
        rev = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", *[f"HEAD:{p}" for p in paths]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        diff = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "HEAD", "--", *paths],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        untracked = subprocess.run(
            [
                "git", "-C", str(repo_root), "ls-files",
                "--others", "--exclude-standard", "--", *paths,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    digest = hashlib.sha256()
    digest.update(rev.encode())
    digest.update(diff.encode())
    digest.update(untracked.encode())
    # `git diff` doesn't include untracked file contents — fold them in too.
    for rel in untracked.splitlines():
        candidate = repo_root / rel.strip()
        if candidate.is_file():
            with contextlib.suppress(OSError):
                digest.update(candidate.read_bytes())
    return digest.hexdigest()[:12]


@pytest.fixture(scope="module")
def workspace_image(e2e_settings) -> Generator[str, None, None]:
    """Ensure the docker workspace runtime image exists locally.

    Uses a content-addressed tag (``agentbox-runtime:e2e-<fingerprint>``) so the
    image is reused when agentbox + the bundled SDKs are unchanged, and rebuilt
    automatically when they change — no per-run rebuild (the build context is the
    whole monorepo, which is slow to transfer) and no stale pinned image. Set
    WORKSPACE_E2E_IMAGE to pin an explicit image (e.g. in CI).
    """
    repo_root = Path(__file__).resolve().parents[5]
    configured_image = os.getenv("WORKSPACE_E2E_IMAGE")
    if configured_image:
        image = configured_image
    else:
        fingerprint = _agentbox_image_fingerprint(repo_root)
        image = (
            f"agentbox-runtime:e2e-{fingerprint}"
            if fingerprint
            else "agentbox-runtime:e2e"
        )

    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    image_present = inspect.returncode == 0
    # Fall back to always-build only when we couldn't fingerprint (floating tag).
    should_build = not image_present or (
        not configured_image and image == "agentbox-runtime:e2e"
    )

    if should_build:
        dockerfile = repo_root / "agentbox" / "Dockerfile.runtime"
        build = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(dockerfile),
                "-t",
                image,
                str(repo_root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            pytest.fail(
                "Workspace e2e failed to build workspace runtime image "
                f"'{image}'.\nSTDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
            )

    yield image


@pytest_asyncio.fixture(scope="function")
async def backend_server(test_app) -> AsyncGenerator[dict[str, str], None]:
    """Run a real backend HTTP server for Docker workspace callbacks."""

    port = _available_port()
    config = uvicorn.Config(
        app=test_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="websockets-sansio",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                exc = server_task.exception()
                raise RuntimeError("Backend server exited before startup") from exc
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Timed out starting backend server")

        docker_base_url = os.getenv(
            "WORKSPACE_E2E_DOCKER_API_URL",
            f"http://host.docker.internal:{port}",
        )
        yield {
            "host_base_url": f"http://127.0.0.1:{port}",
            "docker_base_url": docker_base_url,
        }
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task


@pytest_asyncio.fixture(scope="function")
async def local_agentbox_server(
    workspace_image,
    tmp_path_factory,
    e2e_settings,
) -> AsyncGenerator[dict[str, str], None]:
    """Run the local Docker AgentBox manager used by workspace e2e tests.

    Binds to the session-pinned port (``e2e_settings`` sets ``agentbox_api_url``)
    rather than a fresh per-test port, so the session-scoped worker subprocess --
    which captured its AgentBox URL once at spawn -- reaches whichever per-test
    manager is currently bound to that port.
    """

    parsed_endpoint = urlparse(e2e_settings.agentbox_api_url)
    port = parsed_endpoint.port or _available_port()
    repo_root = Path(__file__).resolve().parents[5]
    agentbox_root = repo_root / "agentbox"
    if str(agentbox_root) not in sys.path:
        sys.path.insert(0, str(agentbox_root))

    state_path = tmp_path_factory.mktemp("agentbox-state") / "state.db"
    storage_root = tmp_path_factory.mktemp("agentbox-workspaces")
    manager_url = e2e_settings.agentbox_api_url
    app_domain = f"127-0-0-1.sslip.io:{port}"
    api_key = e2e_settings.agentbox_api_key
    runtime_platform = os.getenv("AGENTBOX_PLATFORM")

    env_updates = {
        "AGENTBOX_PROVIDER": "docker",
        "AGENTBOX_API_KEY": api_key,
        "AGENTBOX_API_URL": manager_url,
        "AGENTBOX_APP_DOMAIN": app_domain,
        "AGENTBOX_RUNTIME_IMAGE": workspace_image,
        "AGENTBOX_STATE_DB_PATH": str(state_path),
        "AGENTBOX_STORAGE_ROOT": str(storage_root),
        "AGENTBOX_ENDPOINT_HOST": "127.0.0.1",
        "AGENTBOX_E2E_LABEL": "true",
        "AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS": "300",
        "AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS": "300",
        "AGENTBOX_CLEANUP_INTERVAL_SECONDS": "30",
    }
    if runtime_platform:
        env_updates["AGENTBOX_PLATFORM"] = runtime_platform

    original_env = {key: os.environ.get(key) for key in env_updates}
    os.environ.update(env_updates)

    import agentbox.config as agentbox_config
    import agentbox.server as agentbox_server

    original_agentbox_settings = {
        "agentbox_provider": agentbox_config.settings.agentbox_provider,
        "agentbox_api_key": agentbox_config.settings.agentbox_api_key,
        "agentbox_api_url": agentbox_config.settings.agentbox_api_url,
        "agentbox_app_domain": agentbox_config.settings.agentbox_app_domain,
        "agentbox_runtime_image": agentbox_config.settings.agentbox_runtime_image,
        "agentbox_state_db_path": agentbox_config.settings.agentbox_state_db_path,
        "agentbox_storage_root": agentbox_config.settings.agentbox_storage_root,
        "agentbox_endpoint_host": agentbox_config.settings.agentbox_endpoint_host,
        "agentbox_e2e_label": agentbox_config.settings.agentbox_e2e_label,
        "agentbox_platform": agentbox_config.settings.agentbox_platform,
        "agentbox_session_idle_timeout_seconds": (
            agentbox_config.settings.agentbox_session_idle_timeout_seconds
        ),
        "agentbox_sandbox_idle_timeout_seconds": (
            agentbox_config.settings.agentbox_sandbox_idle_timeout_seconds
        ),
        "agentbox_cleanup_interval_seconds": (
            agentbox_config.settings.agentbox_cleanup_interval_seconds
        ),
    }
    agentbox_config.settings.agentbox_provider = "docker"
    agentbox_config.settings.agentbox_api_key = api_key
    agentbox_config.settings.agentbox_api_url = manager_url
    agentbox_config.settings.agentbox_app_domain = app_domain
    agentbox_config.settings.agentbox_runtime_image = workspace_image
    agentbox_config.settings.agentbox_state_db_path = str(state_path)
    agentbox_config.settings.agentbox_storage_root = str(storage_root)
    agentbox_config.settings.agentbox_endpoint_host = "127.0.0.1"
    agentbox_config.settings.agentbox_e2e_label = True
    agentbox_config.settings.agentbox_platform = runtime_platform
    agentbox_config.settings.agentbox_session_idle_timeout_seconds = 300
    agentbox_config.settings.agentbox_sandbox_idle_timeout_seconds = 300
    agentbox_config.settings.agentbox_cleanup_interval_seconds = 30

    config = uvicorn.Config(
        app=agentbox_server.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                exc = server_task.exception()
                raise RuntimeError("Local AgentBox server exited before startup") from exc
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Timed out starting local AgentBox server")

        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(100):
                if server_task.done():
                    exc = server_task.exception()
                    raise RuntimeError(
                        "Local AgentBox server exited before health check"
                    ) from exc
                try:
                    response = await client.get(f"{manager_url}/health")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Timed out waiting for local AgentBox health")

        yield {"manager_base_url": manager_url, "api_key": api_key}
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task
        for key, value in original_agentbox_settings.items():
            setattr(agentbox_config.settings, key, value)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest_asyncio.fixture
async def configure_workspace_api_url(
    backend_server,
    local_agentbox_server,
) -> AsyncGenerator[dict[str, str], None]:
    """Route workspace SDK calls to the per-test backend and local AgentBox manager."""

    from app.modules.workspace.services.workspace_tool_runtime import (
        reset_workspace_tool_runtimes,
    )

    original_api_url = settings.api_url
    original_api_url_env = os.environ.get("API_URL")
    original_manager_url = settings.agentbox_api_url
    original_manager_url_env = os.environ.get("AGENTBOX_API_URL")
    original_manager_key = settings.agentbox_api_key
    original_manager_key_env = os.environ.get("AGENTBOX_API_KEY")

    reset_workspace_tool_runtimes()
    settings.api_url = backend_server["host_base_url"]
    settings.agentbox_api_url = local_agentbox_server["manager_base_url"]
    settings.agentbox_api_key = local_agentbox_server["api_key"]
    os.environ["API_URL"] = backend_server["host_base_url"]
    os.environ["AGENTBOX_API_URL"] = local_agentbox_server["manager_base_url"]
    os.environ["AGENTBOX_API_KEY"] = local_agentbox_server["api_key"]
    try:
        yield {**backend_server, **local_agentbox_server}
    finally:
        reset_workspace_tool_runtimes()
        settings.api_url = original_api_url
        settings.agentbox_api_url = original_manager_url
        settings.agentbox_api_key = original_manager_key
        if original_api_url_env is None:
            os.environ.pop("API_URL", None)
        else:
            os.environ["API_URL"] = original_api_url_env
        if original_manager_url_env is None:
            os.environ.pop("AGENTBOX_API_URL", None)
        else:
            os.environ["AGENTBOX_API_URL"] = original_manager_url_env
        if original_manager_key_env is None:
            os.environ.pop("AGENTBOX_API_KEY", None)
        else:
            os.environ["AGENTBOX_API_KEY"] = original_manager_key_env


@pytest_asyncio.fixture(scope="function")
async def scheduler_api_server(e2e_settings) -> AsyncGenerator[str, None]:
    """Run a real scheduler API server for workflow/schedule e2e tests."""

    from app.scheduler import app as scheduler_app

    port = _available_port()
    original_scheduler_url = settings.scheduler_api_url
    original_scheduler_env = os.environ.get("SCHEDULER_API_URL")
    settings.scheduler_api_url = f"http://127.0.0.1:{port}"
    os.environ["SCHEDULER_API_URL"] = settings.scheduler_api_url

    config = uvicorn.Config(
        app=scheduler_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                exc = server_task.exception()
                raise RuntimeError("Scheduler API server exited before startup") from exc
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Timed out starting scheduler API server")
        yield settings.scheduler_api_url
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task
        settings.scheduler_api_url = original_scheduler_url
        if original_scheduler_env is None:
            os.environ.pop("SCHEDULER_API_URL", None)
        else:
            os.environ["SCHEDULER_API_URL"] = original_scheduler_env


@pytest_asyncio.fixture(scope="function")
async def full_stack(
    configure_workspace_api_url,
    scheduler_api_server,
) -> AsyncGenerator[dict[str, str], None]:
    """The complete stack for fully-real e2e tests.

    Combines the real backend + local Docker AgentBox (``configure_workspace_api_url``)
    and a real scheduler (``scheduler_api_server``) with the **production streaq
    worker subprocess** wired to the AgentBox and the Fireworks-backed
    ``system:lemma`` agent runtime. The worker is a fresh subprocess per test
    (no shared in-process singletons), so triggered runs execute real functions
    in Docker and real agents on Fireworks autonomously.

    Skips when the Fireworks credential is unavailable.
    """
    import redis.asyncio as redis

    fireworks_key = (
        os.getenv("FIREWORKS_API_KEY")
        or _read_root_env_value("FIREWORKS_API_KEY")
        or _read_root_env_value("lemma_openai_api_key")
    )
    if not fireworks_key:
        pytest.skip(
            "Fireworks credential unavailable; export FIREWORKS_API_KEY to run "
            "fully-real agent e2e tests."
        )

    # Inject the Fireworks key so both the in-process backend and the worker
    # subprocess resolve the system:lemma OpenAI-compatible profile. The model
    # catalog has no built-in default, so the real-LLM env (.env / exported
    # LEMMA_OPENAI_* vars) must provide LEMMA_OPENAI_BASE_URL + LEMMA_OPENAI_MODEL_NAMES.
    cred_env_keys = ("LEMMA_OPENAI_API_KEY", "lemma_openai_api_key")
    original_cred_env = {key: os.environ.get(key) for key in cred_env_keys}
    original_cred_setting = settings.lemma_openai_api_key
    for key in cred_env_keys:
        os.environ[key] = fireworks_key
    settings.lemma_openai_api_key = fireworks_key

    redis_url = settings.redis_url
    redis_client = redis.from_url(redis_url, decode_responses=False)
    await redis_client.flushdb()
    await redis_client.aclose()

    backend_root = Path(__file__).resolve().parents[4]
    log_path = f"/tmp/lemma_full_stack_worker_{uuid.uuid4().hex}.log"

    # The worker subprocess inherits os.environ, which now carries AGENTBOX_API_URL/
    # KEY and API_URL (from configure_workspace_api_url), SCHEDULER_API_URL (from
    # scheduler_api_server), and the Fireworks key. So it runs functions in the
    # local Docker AgentBox and agents on Fireworks for real.
    worker_env = {
        **os.environ,
        "PYTHONPATH": ".",
        "DATABASE_URL": settings.database_url,
        "DATASTORE_DATABASE_URL": settings.datastore_database_url,
        "REDIS_URL": redis_url,
        "SUPERTOKENS_CORE_URL": settings.supertokens_core_url,
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": settings.email_output_dir,
        "GCS_STORAGE_BUCKET": "",
        "PUBLIC_BUCKET_NAME": "",
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_OBJECT_STORAGE_ROOT": settings.local_object_storage_root,
        "LOCAL_FILE_STORAGE_ROOT": settings.local_file_storage_root,
        "COMPOSIO_CACHE_DIR": "/tmp/composio",
    }

    with open(log_path, "w+") as log_file:
        proc = subprocess.Popen(
            [str(backend_root / ".venv/bin/streaq"), "run", "app.events:streaq_worker"],
            cwd=str(backend_root),
            env=worker_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        readiness_markers = (
            "Worker starting...",
            "`HandleAgentRunEvent` waiting for messages",
            "`HandleScheduleEvents` waiting for messages",
        )
        startup_ok = False
        for _ in range(300):
            if proc.poll() is not None:
                log_file.flush()
                log_file.seek(0)
                pytest.fail(
                    "full_stack worker exited before startup "
                    f"(code={proc.returncode}).\n{log_file.read()}"
                )
            log_file.flush()
            log_file.seek(0)
            logs = log_file.read()
            if all(marker in logs for marker in readiness_markers):
                startup_ok = True
                break
            await asyncio.sleep(0.1)
        if not startup_ok:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_file.flush()
            log_file.seek(0)
            pytest.fail(f"Timed out waiting for full_stack worker.\n{log_file.read()}")

        try:
            yield {
                "host_base_url": configure_workspace_api_url["host_base_url"],
                "docker_base_url": configure_workspace_api_url["docker_base_url"],
                "manager_base_url": configure_workspace_api_url["manager_base_url"],
            }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            redis_client = redis.from_url(redis_url, decode_responses=False)
            await redis_client.flushdb()
            await redis_client.aclose()
            settings.lemma_openai_api_key = original_cred_setting
            for key, value in original_cred_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
