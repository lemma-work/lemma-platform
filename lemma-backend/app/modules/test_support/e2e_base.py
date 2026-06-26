"""Shared E2E fixtures for module-local test conftest files."""

from __future__ import annotations

import os
import socket
from pathlib import Path
import shutil
import subprocess
import sys
import asyncio
import logging
from typing import TYPE_CHECKING, AsyncGenerator, Any, Callable
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.test_utils import (
    create_postgres_database,
    get_postgres_container,
    get_postgres_url,
    get_redis_container,
    get_redis_url,
    get_supertokens_container,
    get_supertokens_url,
    get_test_network,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


os.makedirs("/tmp/composio", exist_ok=True)
os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

_SHARED_CONTEXTS: dict[str, Any] = {}
_SHARED_RESOURCES: dict[str, Any] = {}
logger = logging.getLogger(__name__)


def _xdist_worker_suffix() -> str:
    """Per-worker suffix for filesystem paths under pytest-xdist.

    Returns "" when running serially (no xdist) and e.g. "-gw0" per worker so
    parallel workers get isolated /tmp roots and don't clobber each other.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    return f"-{worker}" if worker else ""


def _ensure_repo_root_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _cleanup_e2e_workspace_containers() -> None:
    """Remove leftover workspace containers created by e2e runs."""
    if not shutil.which("docker"):
        return

    ps = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=lemma.e2e=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    container_ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if container_ids:
        subprocess.run(["docker", "rm", "-f", *container_ids], check=False)


def _configure_local_datastore_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.core.config import settings

    del tmp_path  # see below
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    # Do NOT override local_object_storage_root with a per-test tmp_path: the
    # session-scoped streaq worker (which auto-indexes uploaded files) uses the
    # session root, so a per-test root meant the worker could never find files
    # the API wrote — indexing failed ("Storage object not found") whenever the
    # worker won the processing race under load. Keep the per-xdist-worker
    # session root (set in e2e_settings); pods are namespaced by id, so tests
    # don't collide on storage.


async def _run_cleanup_step(
    name: str,
    cleanup: Callable[[], Any],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    try:
        await asyncio.wait_for(cleanup(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("Timed out during E2E cleanup step %s", name)


def _shared_context_resource(name: str, factory: Callable[[], Any]) -> Any:
    """Reuse expensive session-scoped container resources across module conftests."""

    if name not in _SHARED_RESOURCES:
        context = factory()
        _SHARED_CONTEXTS[name] = context
        _SHARED_RESOURCES[name] = context.__enter__()
    return _SHARED_RESOURCES[name]


def _close_shared_contexts() -> None:
    for name, context in reversed(_SHARED_CONTEXTS.items()):
        exit_method = getattr(context, "__exit__", None)
        if exit_method is not None:
            exit_method(None, None, None)
    _SHARED_CONTEXTS.clear()
    _SHARED_RESOURCES.clear()


def _reset_supertokens_testing_state() -> None:
    from supertokens_python.recipe.dashboard.recipe import DashboardRecipe
    from supertokens_python.recipe.emailpassword.recipe import EmailPasswordRecipe
    from supertokens_python.recipe.jwt.recipe import JWTRecipe
    from supertokens_python.recipe.multitenancy.recipe import MultitenancyRecipe
    from supertokens_python.recipe.oauth2provider.recipe import OAuth2ProviderRecipe
    from supertokens_python.recipe.openid.recipe import OpenIdRecipe
    from supertokens_python.recipe.session.recipe import SessionRecipe
    from supertokens_python.recipe.thirdparty.recipe import ThirdPartyRecipe
    from supertokens_python.recipe.usermetadata.recipe import UserMetadataRecipe
    from supertokens_python.supertokens import Supertokens

    Supertokens.reset()
    for recipe in (
        SessionRecipe,
        EmailPasswordRecipe,
        DashboardRecipe,
        ThirdPartyRecipe,
        JWTRecipe,
        OpenIdRecipe,
        MultitenancyRecipe,
        UserMetadataRecipe,
        OAuth2ProviderRecipe,
    ):
        recipe.reset()


@pytest.fixture(scope="session")
def test_network():
    yield _shared_context_resource("network", get_test_network)


@pytest.fixture(scope="session")
def postgres_container(test_network):
    def _factory():
        return get_postgres_container(network=test_network)

    postgres = _shared_context_resource("postgres", _factory)
    if not getattr(postgres, "_lemma_datastore_database_created", False):
        create_postgres_database(postgres, "datastore")
        setattr(postgres, "_lemma_datastore_database_created", True)
    yield postgres


@pytest.fixture(scope="session")
def supertokens_container():
    yield _shared_context_resource("supertokens", get_supertokens_container)


@pytest.fixture(scope="session")
def redis_container():
    yield _shared_context_resource("redis", get_redis_container)


@pytest.fixture(scope="session")
def test_database_url(postgres_container) -> str:
    return get_postgres_url(postgres_container)


@pytest.fixture(scope="session")
def test_redis_url(redis_container) -> str:
    return get_redis_url(redis_container)


@pytest.fixture(scope="session")
def e2e_settings(test_database_url, test_redis_url, supertokens_container):
    from app.core.config import settings

    os.environ["SUPERTOKENS_ENV"] = "testing"
    settings.database_url = test_database_url
    base_url = test_database_url.rsplit("/", 1)[0]
    settings.datastore_database_url = f"{base_url}/datastore"
    settings.redis_url = test_redis_url
    settings.supertokens_core_url = get_supertokens_url(supertokens_container)
    settings.environment = "testing"
    settings.debug = True
    settings.google_client_id = "test-google-client-id"
    settings.google_client_secret = "test-google-client-secret"
    settings.email_transport = "filesystem"
    # Namespace local filesystem roots per pytest-xdist worker so parallel
    # workers never share (or rmtree out from under each other) the same dirs.
    # ``PYTEST_XDIST_WORKER`` is e.g. "gw0"/"gw1" under xdist, unset otherwise.
    worker_suffix = _xdist_worker_suffix()
    settings.email_output_dir = f"/tmp/lemma-test-emails{worker_suffix}"
    shutil.rmtree(settings.email_output_dir, ignore_errors=True)
    Path(settings.email_output_dir).mkdir(parents=True, exist_ok=True)
    settings.local_file_storage_root = f"/tmp/lemma-files-tests{worker_suffix}"
    settings.gcs_storage_bucket = None
    settings.public_bucket_name = None
    settings.storage_backend = "local"
    settings.embedding_provider = "local"
    settings.local_object_storage_root = f"/tmp/lemma-object-storage-tests{worker_suffix}"

    # Pin a stable, session-wide AgentBox manager endpoint. The worker subprocess
    # is session-scoped and captures os.environ once at spawn, while the manager
    # (``local_agentbox_server``) is recreated per test -- if its port changed per
    # test the worker would point at a dead port and every worker-driven job would
    # fail with ConnectError. Fixing the port here (set before the worker spawns,
    # since the worker depends on this fixture) lets the manager rebind to it each
    # test and keeps the worker's captured URL valid for the whole run.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        agentbox_port = int(sock.getsockname()[1])
    agentbox_url = f"http://127.0.0.1:{agentbox_port}"
    agentbox_key = settings.agentbox_api_key or "e2e-agentbox-key"
    settings.agentbox_api_url = agentbox_url
    settings.agentbox_api_key = agentbox_key
    os.environ["AGENTBOX_API_URL"] = agentbox_url
    os.environ["AGENTBOX_API_KEY"] = agentbox_key

    # E2E execution mode: default to the fast mocked level (no real model, no
    # Docker) so CI and local runs are fast and deterministic. ``E2E_REAL=1``
    # (or the per-axis E2E_LLM_MODE / E2E_SANDBOX_MODE) opts into the real model
    # + Docker AgentBox. Set on os.environ too so the worker subprocess (which
    # inherits os.environ) runs in the same mode.
    real = os.environ.get("E2E_REAL", "").lower() in ("1", "true", "yes")
    llm_mode = os.environ.get("E2E_LLM_MODE") or ("real" if real else "mock")
    sandbox_mode = os.environ.get("E2E_SANDBOX_MODE") or ("docker" if real else "fake")
    settings.e2e_llm_mode = llm_mode
    settings.e2e_sandbox_mode = sandbox_mode
    os.environ["E2E_LLM_MODE"] = llm_mode
    os.environ["E2E_SANDBOX_MODE"] = sandbox_mode

    # A single Kreuzberg is shared across all xdist workers (see datastore
    # conftest); under concurrent indexing load it can briefly stall or be
    # OOM-restarted by host memory pressure. Allow more transient retries than
    # the prod default (5) so extraction rides that out instead of failing.
    # Set on os.environ so the worker subprocess (which indexes) inherits it.
    os.environ.setdefault("KREUZBERG_TRANSIENT_RETRY_ATTEMPTS", "8")

    # e2e indexes datastore files explicitly in-process (the index_file helper).
    # Disable the worker's auto-index-on-upload so it doesn't ALSO index every
    # uploaded file through the single shared Kreuzberg — that double load OOMs
    # the container under -n2. Inherited by the worker subprocess.
    settings.e2e_disable_worker_file_autoindex = True
    os.environ.setdefault("E2E_DISABLE_WORKER_FILE_AUTOINDEX", "true")

    if sandbox_mode == "fake":
        _start_fake_agentbox(agentbox_port)

    from app.core.infrastructure.db import session as db_session_module

    db_session_module.reset_engine_state()

    return settings


_fake_agentbox_started: set[int] = set()


def _start_fake_agentbox(port: int) -> None:
    """Run the in-process fake AgentBox manager on ``port`` in a daemon thread.

    One per session/xdist-worker (the pinned agentbox port is per-worker), so
    parallel workers don't contend. Replaces the Docker manager for the fast
    ``e2e_sandbox_mode == "fake"`` path; the session worker + in-process tools
    reach it over HTTP at the pinned port.
    """
    if port in _fake_agentbox_started:
        return
    import threading
    import time

    import uvicorn

    from app.modules.workspace.testing.fake_agentbox import create_fake_agentbox_app

    app = create_fake_agentbox_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name=f"fake-agentbox-{port}", daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    _fake_agentbox_started.add(port)


@pytest.fixture(scope="session", autouse=True)
def cleanup_workspace_containers_session():
    # Clean stale containers from prior interrupted runs.
    _cleanup_e2e_workspace_containers()
    yield
    _cleanup_e2e_workspace_containers()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def cleanup_workspace_containers_function():
    yield
    _cleanup_e2e_workspace_containers()
    from app.core.infrastructure.channels.channel_service import channel_service
    from app.core.infrastructure.db.session import close_engine
    from app.core.infrastructure.events.message_bus import close_message_bus
    from app.core.infrastructure.jobs.streaq_job_queue import close_streaq_job_queue
    from app.modules.datastore.infrastructure.session import close_datastore_engine
    from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
        close_surface_event_dedup_store,
    )
    from app.modules.identity.infrastructure.user_cache import close_user_cache
    from app.modules.workspace.services.workspace_sandbox_service import (
        reset_workspace_store_state,
    )
    from app.modules.workspace.services.workspace_tool_runtime import (
        reset_workspace_tool_runtimes,
    )

    reset_workspace_tool_runtimes()
    await _run_cleanup_step("reset_workspace_store_state", reset_workspace_store_state)
    await _run_cleanup_step("close_surface_event_dedup_store", close_surface_event_dedup_store)
    await _run_cleanup_step("close_user_cache", close_user_cache)
    await _run_cleanup_step("close_streaq_job_queue", close_streaq_job_queue)
    await _run_cleanup_step("close_message_bus", close_message_bus)
    await _run_cleanup_step("channel_service.disconnect", channel_service.disconnect)
    await _run_cleanup_step("close_datastore_engine", close_datastore_engine)
    await _run_cleanup_step("close_engine", close_engine)


@pytest_asyncio.fixture(scope="session")
async def worker(e2e_settings):
    """Run the real streaq worker process used in production.

    Session-scoped: one worker subprocess for the whole run instead of spawning
    (and tearing down) a fresh one per test. The schema is created by the first
    test's db_manager and never dropped mid-run, so the worker's connections stay
    valid for its whole lifetime.

    The worker does NOT need Kreuzberg: e2e disables the worker's auto-index of
    uploads (e2e_disable_worker_file_autoindex) and indexes in-process instead, so
    no Kreuzberg URL is wired into the worker subprocess.
    """
    import asyncio
    import redis.asyncio as redis

    redis_client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
    await redis_client.flushdb()
    await redis_client.aclose()

    log_path = f"/tmp/lemma_e2e_worker_{uuid4().hex}.log"
    backend_root = Path(__file__).resolve().parents[3]
    with open(log_path, "w+") as log_file:
        # Forward LEMMA_OPENAI_* (and other LEMMA_*) vars from the backend
        # .env file so that the worker subprocess can call the system:lemma
        # LLM provider even when those vars aren't set in the shell env.
        # os.environ takes precedence over .env (allows CI override).
        from app.modules.agent.tests.e2e.system_lemma_helpers import (
            system_lemma_env_overlay,
        )

        proc = subprocess.Popen(
            [
                str(backend_root / ".venv/bin/streaq"),
                "run",
                "app.events:streaq_worker",
            ],
            cwd=str(backend_root),
            env={
                **os.environ,
                **system_lemma_env_overlay(),  # LEMMA_OPENAI_* from .env
                "PYTHONPATH": ".",
                "DATABASE_URL": e2e_settings.database_url,
                "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
                "REDIS_URL": e2e_settings.redis_url,
                "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
                # The manager rebinds to this stable port each test; keep the
                # worker pointed at it so worker-driven function jobs reach it.
                "AGENTBOX_API_URL": e2e_settings.agentbox_api_url,
                "AGENTBOX_API_KEY": e2e_settings.agentbox_api_key,
                "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
                "ENVIRONMENT": "testing",
                "DEBUG": "true",
                "EMAIL_TRANSPORT": "filesystem",
                "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
                "GCS_STORAGE_BUCKET": "",
                "PUBLIC_BUCKET_NAME": "",
                "STORAGE_BACKEND": "local",
                "EMBEDDING_PROVIDER": "local",
                "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
                "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
                "COMPOSIO_CACHE_DIR": "/tmp/composio",
            },
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
        for _ in range(200):
            if proc.poll() is not None:
                log_file.flush()
                log_file.seek(0)
                logs = log_file.read()
                pytest.fail(
                    f"streaq worker exited before startup (code={proc.returncode}).\n{logs}"
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
            logs = log_file.read()
            pytest.fail(f"Timed out waiting for streaq worker startup.\n{logs}")

        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            redis_client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
            await redis_client.flushdb()
            await redis_client.aclose()


@pytest_asyncio.fixture(scope="function")
async def db_manager(e2e_settings) -> AsyncGenerator[DatabaseManager, None]:
    # Per-test, but cheap: the schema is created once (create_all is idempotent
    # via checkfirst) and persists for the whole run, so each test only pays a
    # fast TRUNCATE for data isolation instead of a full drop/create. Keeping the
    # schema stable also lets the shared streaq worker hold its connections.
    _ensure_repo_root_on_path()
    manager = DatabaseManager(e2e_settings.database_url)

    from app.modules.identity.infrastructure.models import (
        user_models,
        organization_models,
    )
    from app.modules.pod.infrastructure.models import pod_models
    from app.modules.agent.infrastructure import models as agent_models
    from app.modules.datastore.infrastructure.models import datastore_models
    from app.modules.workflow.infrastructure import models as workflow_models
    from app.modules.function.infrastructure import models as function_models
    from app.modules.apps.infrastructure import models as app_models
    from app.modules.connectors.infrastructure import models as connector_models
    from app.modules.schedule.infrastructure import models as schedule_models
    from app.modules.usage.infrastructure import models as usage_models
    from app.modules.agent_surfaces.infrastructure import models as agent_surface_models
    from app.modules.pod.infrastructure import models as pod_role_models
    _ = (
        user_models,
        organization_models,
        pod_models,
        agent_models,
        datastore_models,
        workflow_models,
        function_models,
        app_models,
        connector_models,
        schedule_models,
        usage_models,
        agent_surface_models,
        pod_role_models,
    )

    import asyncio as _asyncio

    from sqlalchemy.exc import DBAPIError

    # The shared session worker runs agent/datastore transactions concurrently
    # with this per-test setup; its row writes can deadlock the truncation DELETE
    # (or the advisory-locked CREATE EXTENSION), and Postgres aborts one side as
    # the victim. Retry the whole setup — the conflicting transaction clears
    # within a beat — instead of failing a random test's setup each run.
    last_exc: BaseException | None = None
    for _attempt in range(6):
        try:
            async with manager.engine.begin() as conn:
                # Serialize with PostgresSearchService.ensure_schema(), which also
                # runs CREATE EXTENSION under this advisory key (concurrent
                # CREATE EXTENSION on pg_extension otherwise deadlocks). Key must
                # match _ENSURE_SCHEMA_LOCK_KEY in postgres_search_service.
                await conn.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x6C656D6D61}
                )
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # Idempotent (checkfirst) — creates schema on the first test, no-ops after.
            await manager.create_tables()
            # Start each test from a clean slate without dropping the schema.
            await manager.truncate_all()
            break
        except DBAPIError as exc:
            message = str(exc).lower()
            if "deadlock" not in message and "lock" not in message:
                raise
            last_exc = exc
            await _asyncio.sleep(0.3 * (_attempt + 1))
    else:
        assert last_exc is not None
        raise last_exc

    yield manager
    await manager.close()


# Factory for the e2e app. Defaults to the OSS app; lemma-cloud overrides this
# (in its conftest, via set_test_app_factory) to compose CLOUD_MODULES so its
# billing e2e suite exercises a billing-aware app.
_test_app_factory = None


def set_test_app_factory(factory) -> None:
    """Override how the e2e ``test_app`` fixture builds its FastAPI app."""
    global _test_app_factory
    _test_app_factory = factory


@pytest.fixture(scope="function")
def test_app(e2e_settings, db_manager, monkeypatch, tmp_path):
    _ensure_repo_root_on_path()
    _configure_local_datastore_runtime(monkeypatch, tmp_path)
    _reset_supertokens_testing_state()
    if _test_app_factory is not None:
        return _test_app_factory()
    from app.app import create_app

    return create_app()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_manager) -> AsyncGenerator:
    async with db_manager.session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def async_client(test_app) -> AsyncGenerator["AsyncClient", None]:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def fixed_test_user(async_client: "AsyncClient"):
    email = f"test+module-e2e-{uuid4().hex[:10]}@example.com"
    password = "TestPassword@123"

    signup_data = {
        "formFields": [
            {"id": "email", "value": email},
            {"id": "password", "value": password},
        ]
    }
    response = await async_client.post("/st/auth/signup", json=signup_data)
    data = response.json()
    assert response.status_code == 200 and data.get("status") == "OK", data

    access_token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert access_token

    return {"email": email, "token": access_token, "id": data["user"]["id"]}


@pytest_asyncio.fixture(scope="function")
async def authenticated_client(
    async_client: "AsyncClient", fixed_test_user
) -> AsyncGenerator["AsyncClient", None]:
    async_client.headers.update({"Authorization": f"Bearer {fixed_test_user['token']}"})
    yield async_client


@pytest_asyncio.fixture(scope="function")
async def fixed_test_org(authenticated_client: "AsyncClient"):
    response = await authenticated_client.post(
        "/organizations",
        json={"name": f"Module Test Org {uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def sample_pod_entity():
    from app.modules.pod.domain.pod_entities import PodEntity, PodStatus, PodType

    return PodEntity(
        name=f"Test Pod {uuid4().hex[:8]}",
        slug=f"test-pod-{uuid4().hex[:8]}",
        description="A test pod",
        status=PodStatus.ACTIVE,
        type=PodType.AUTOMATION,
        user_id=uuid4(),
        organization_id=uuid4(),
    )
