"""Shared E2E fixtures for module-local test conftest files."""

from __future__ import annotations

from app.modules.workspace.config import workspace_settings

import os
import json
import socket
from concurrent.futures import ThreadPoolExecutor
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
    get_postgres_uri_from_another_container,
    get_postgres_url,
    get_redis_url,
    get_supertokens_container,
    get_supertokens_url,
    shared_postgres,
    shared_redis,
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


def _cleanup_e2e_workspace_containers(*, sandboxes_only: bool = False) -> None:
    """Remove leftover Docker containers created by e2e runs.

    The shared session testcontainers (postgres/redis/supertokens/kreuzberg) and
    the sandboxes BOTH carry ``lemma.e2e=true``, so a broad sweep by that label
    would tear down the live session containers mid-run. Sandboxes are
    identifiable on their own: the workspace module labels its containers
    ``managed-by=lemma-workspace``, and pre-cutover ones carry
    ``app.kubernetes.io/name=lemma-sandbox``.

    - ``sandboxes_only=True`` (per-test cleanup): remove ONLY sandboxes, sparing
      the shared session containers — otherwise the workspace teardown after the
      first test kills postgres/redis and every later test fails to connect.
    - default (session boundaries): remove ALL e2e-labeled containers for a clean
      slate, which is safe because the session containers aren't up yet (start)
      or are being torn down anyway (finish).
    """
    if not shutil.which("docker"):
        return

    # Docker's filters are conjunctive, so each way of labelling a sandbox has
    # to be swept separately.
    #
    # Note this sweep is machine-wide, not run-scoped: two e2e runs sharing a
    # Docker daemon will destroy each other's sandboxes, which surfaces as
    # "container is marked for removal and cannot be started" in whichever one
    # loses. That is fine for a single run and for CI's one-runner-per-job
    # layout; it is a trap for anyone running two suites side by side.
    filter_sets = [["--filter", "label=lemma.e2e=true"]]
    if sandboxes_only:
        filter_sets = [
            ["--filter", "label=lemma.e2e=true",
             "--filter", "label=app.kubernetes.io/name=lemma-sandbox"],
            ["--filter", "label=managed-by=lemma-workspace"],
        ]
    else:
        filter_sets.append(["--filter", "label=managed-by=lemma-workspace"])

    container_ids: list[str] = []
    for label_filters in filter_sets:
        ps = subprocess.run(
            ["docker", "ps", "-aq", *label_filters],
            capture_output=True,
            text=True,
            check=False,
        )
        container_ids += [
            line.strip() for line in ps.stdout.splitlines() if line.strip()
        ]
    if container_ids:
        # -v also removes each container's anonymous data volume (postgres,
        # supertokens, and kreuzberg all declare VOLUME in their image) — every
        # sweep that ran without it leaked one volume per container, forever.
        subprocess.run(
            ["docker", "rm", "-f", "-v", *sorted(set(container_ids))], check=False
        )

    if sandboxes_only:
        return
    # At session boundaries also prune orphaned ``lemma-e2e-*`` networks left by
    # interrupted runs from before the shared-Postgres model (each was its own
    # Docker network and consumed a subnet from the default address pool —
    # accumulating them eventually exhausts the pool: "all predefined address
    # pools have been fully subnetted", and every later run fails at ``docker
    # network create``). Nothing creates these anymore, so this is a no-op once
    # any stragglers are gone; kept as a cheap defensive sweep.
    nets = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", "name=lemma-e2e"],
        capture_output=True,
        text=True,
        check=False,
    )
    network_ids = [line.strip() for line in nets.stdout.splitlines() if line.strip()]
    if network_ids:
        subprocess.run(["docker", "network", "rm", *network_ids], check=False)


def _configure_local_datastore_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        logger.warning("test_support.e2e_base.timed_out_during_e2e_cleanup.timeout")


async def _close_e2e_process_clients() -> None:
    """Close process-local clients opened by HTTPX ASGI test requests.

    ``httpx.ASGITransport`` intentionally does not run the application's
    lifespan. E2E requests can therefore initialize the same lazy singletons as
    production without invoking their production shutdown hooks. Keep this
    cleanup on the pytest async loop and call it from the canonical client
    fixture after all dependent request fixtures have unwound.
    """

    from app.core.infrastructure.cache.redis_json_cache import close_redis_json_caches
    from app.core.infrastructure.channels.channel_service import channel_service
    from app.core.infrastructure.db.session import close_engine
    from app.core.infrastructure.jobs.streaq_job_queue import close_streaq_job_queue
    from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
        close_surface_event_dedup_store,
    )
    from app.modules.datastore.infrastructure.session import close_datastore_engine
    from app.modules.identity.infrastructure.user_cache import close_user_cache
    from app.modules.identity.services.auth_abuse import close_auth_abuse_store
    from app.modules.identity.services.telegram_oidc import close_telegram_oidc_store
    from app.modules.workspace.services.workspace_sandbox_service import (
        reset_workspace_store_state,
    )
    from app.modules.workspace.services.workspace_tool_runtime import (
        close_workspace_tool_runtimes,
    )

    await _run_cleanup_step(
        "close_workspace_tool_runtimes", close_workspace_tool_runtimes
    )
    await _run_cleanup_step("reset_workspace_store_state", reset_workspace_store_state)
    await _run_cleanup_step(
        "close_surface_event_dedup_store", close_surface_event_dedup_store
    )
    await _run_cleanup_step("close_user_cache", close_user_cache)
    await _run_cleanup_step("close_auth_abuse_store", close_auth_abuse_store)
    await _run_cleanup_step("close_telegram_oidc_store", close_telegram_oidc_store)
    await _run_cleanup_step("close_streaq_job_queue", close_streaq_job_queue)
    await _run_cleanup_step("close_redis_json_caches", close_redis_json_caches)
    await _run_cleanup_step("channel_service.disconnect", channel_service.disconnect)
    await _run_cleanup_step("close_datastore_engine", close_datastore_engine)
    await _run_cleanup_step("close_engine", close_engine)


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
    from supertokens_python.recipe.accountlinking.recipe import AccountLinkingRecipe
    from supertokens_python.recipe.dashboard.recipe import DashboardRecipe
    from supertokens_python.recipe.emailpassword.recipe import EmailPasswordRecipe
    from supertokens_python.recipe.emailverification.recipe import (
        EmailVerificationRecipe,
    )
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
        AccountLinkingRecipe,
        EmailPasswordRecipe,
        EmailVerificationRecipe,
        DashboardRecipe,
        ThirdPartyRecipe,
        JWTRecipe,
        OpenIdRecipe,
        MultitenancyRecipe,
        UserMetadataRecipe,
        OAuth2ProviderRecipe,
    ):
        recipe.reset()


async def verify_emailpassword_for_tests(user_id: str, email: str) -> None:
    """Complete the real SuperTokens verification transition in E2E fixtures."""
    from supertokens_python.recipe.emailverification.asyncio import (
        create_email_verification_token,
        verify_email_using_token,
    )
    from supertokens_python.recipe.emailverification.interfaces import (
        CreateEmailVerificationTokenOkResult,
    )
    from supertokens_python.types import RecipeUserId

    created = await create_email_verification_token(
        "public", RecipeUserId(user_id), email
    )
    assert isinstance(created, CreateEmailVerificationTokenOkResult)
    verified = await verify_email_using_token(
        "public", created.token, attempt_account_linking=False
    )
    assert verified.status == "OK"


def _sanitize_worker_id(worker_id: str) -> str:
    import re

    return re.sub(r"[^0-9a-zA-Z_]", "_", worker_id)


def _postgres_worker_db_name(worker_id: str) -> str:
    return f"lemma_e2e_{_sanitize_worker_id(worker_id)}"


def _redis_worker_db_index(worker_id: str) -> int:
    """Map an xdist worker id to a small Redis logical-DB index.

    Redis has a native per-connection "logical database" concept (``SELECT
    <n>``, or ``redis://host:port/<n>``) -- unlike Postgres there's no
    ``CREATE DATABASE``-equivalent step, so each worker just gets routed to
    its own index on the one shared server. "master" (no xdist) -> 0,
    "gw0" -> 1, "gw1" -> 2, etc.

    Redis ships with only 16 databases (0-15) by default, so this fails
    loudly rather than silently colliding two workers on the same index if
    parallelism ever grows past that. The widest matrix in
    ``.github/workflows/e2e.yml`` today is ``-n 3``, well under the ceiling;
    if that ever changes, either raise ``databases`` in the shared
    container's redis.conf or shrink worker counts.
    """
    if worker_id == "master":
        return 0
    import re

    match = re.search(r"(\d+)$", worker_id)
    if not match:
        raise RuntimeError(
            f"Cannot derive a Redis DB index from xdist worker id {worker_id!r} "
            "(expected 'master' or a 'gwN' id)."
        )
    index = int(match.group(1)) + 1
    if index > 15:
        raise RuntimeError(
            f"xdist worker {worker_id!r} maps to Redis DB index {index}, but "
            "Redis only ships 16 logical databases (0-15) by default. Reduce "
            "xdist parallelism or raise `databases` in the shared Redis "
            "container's config."
        )
    return index


def _postgres_worker_datastore_db_name(worker_id: str) -> str:
    return f"datastore_{_sanitize_worker_id(worker_id)}"


def _postgres_worker_supertokens_db_name(worker_id: str) -> str:
    return f"supertokens_{_sanitize_worker_id(worker_id)}"


def _start_postgres(worker_id: str, basetemp_parent) -> None:
    """Connect to the ONE Postgres server shared across all xdist workers.

    Each worker gets its own logical database inside it (created via
    ``create_postgres_database``), rather than its own full container -- one
    `docker run postgres` and one idle Postgres server process for the whole
    run instead of N. See ``shared_postgres`` in test_utils for the
    coordination mechanism (mirrors ``shared_kreuzberg``).
    """

    def _factory():
        return shared_postgres(basetemp_parent, worker_id)

    postgres = _shared_context_resource("postgres", _factory)
    if not getattr(postgres, "_lemma_worker_databases_created", False):
        create_postgres_database(postgres, _postgres_worker_db_name(worker_id))
        create_postgres_database(
            postgres, _postgres_worker_datastore_db_name(worker_id)
        )
        create_postgres_database(
            postgres, _postgres_worker_supertokens_db_name(worker_id)
        )
        setattr(postgres, "_lemma_worker_databases_created", True)


def _start_supertokens(worker_id: str, basetemp_parent) -> None:
    """Start this worker's SuperTokens container against real Postgres.

    Depends on postgres (needs it running, plus this worker's supertokens_*
    database to exist before the core's first connection) -- unlike
    postgres/redis, deliberately NOT threaded alongside the other two in
    _warm_shared_containers; see the comment there.
    """
    _start_postgres(worker_id, basetemp_parent)
    postgres = _SHARED_RESOURCES["postgres"]
    postgres_uri = get_postgres_uri_from_another_container(
        postgres, _postgres_worker_supertokens_db_name(worker_id)
    )

    def _factory():
        return get_supertokens_container(postgres_uri)

    _shared_context_resource("supertokens", _factory)


def _start_redis(worker_id: str, basetemp_parent) -> None:
    """Connect to the ONE Redis server shared across all xdist workers.

    Each worker gets its own logical DB index inside it (selected via the
    connection URL, see ``_redis_worker_db_index``), rather than its own full
    container -- one `docker run redis` for the whole run instead of N. See
    ``shared_redis`` in test_utils for the coordination mechanism (mirrors
    ``shared_postgres``/``shared_kreuzberg``). Unlike Postgres, no
    per-worker provisioning step is needed here -- Redis DBs exist by index
    already, nothing to create.
    """

    def _factory():
        return shared_redis(basetemp_parent, worker_id)

    _shared_context_resource("redis", _factory)


def _warm_shared_containers(worker_id: str, tmp_path_factory) -> None:
    """Boot postgres(+supertokens) and redis concurrently on first use.

    postgres and redis are independent Docker containers with nothing to
    wait on each other for, so they're threaded -- `subprocess.run` and the
    HTTP/TCP health polls all release the GIL while waiting, so this is real
    concurrency, not just interleaving. supertokens now runs against real
    Postgres (see _start_supertokens), so it's resolved sequentially AFTER
    postgres within the same job rather than threaded alongside it -- it
    needs postgres already running, with this worker's supertokens_*
    database already created, before its own first connection.
    `_shared_context_resource` is the dedupe layer, so a second caller (or a
    test that only needs one of the three) always gets the same cached
    instance.
    """
    if all(
        name in _SHARED_RESOURCES for name in ("postgres", "redis", "supertokens")
    ):
        return

    # Resolve once on this (the calling) thread before fanning out to the
    # pool below. ``TempPathFactory.getbasetemp()`` lazily creates and caches
    # the base temp dir on first call and is not safe to invoke from two
    # threads at once -- postgres and redis both used to derive this
    # independently inside their own worker thread, and running both jobs
    # concurrently raced two `mkdir`s for the same path, one losing with
    # ``FileExistsError``.
    basetemp_parent = tmp_path_factory.getbasetemp().parent

    jobs: list[Callable[[], Any]] = []
    if "postgres" not in _SHARED_RESOURCES or "supertokens" not in _SHARED_RESOURCES:
        jobs.append(lambda: _start_supertokens(worker_id, basetemp_parent))
    if "redis" not in _SHARED_RESOURCES:
        jobs.append(lambda: _start_redis(worker_id, basetemp_parent))

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for future in [pool.submit(job) for job in jobs]:
            future.result()


@pytest.fixture(scope="session")
def postgres_container(worker_id, tmp_path_factory):
    _warm_shared_containers(worker_id, tmp_path_factory)
    yield _SHARED_RESOURCES["postgres"]


@pytest.fixture(scope="session")
def supertokens_container(worker_id, tmp_path_factory):
    _warm_shared_containers(worker_id, tmp_path_factory)
    yield _SHARED_RESOURCES["supertokens"]


@pytest.fixture(scope="session")
def redis_container(worker_id, tmp_path_factory):
    _warm_shared_containers(worker_id, tmp_path_factory)
    yield _SHARED_RESOURCES["redis"]


@pytest.fixture(scope="session")
def test_database_url(postgres_container, worker_id) -> str:
    return get_postgres_url(postgres_container, _postgres_worker_db_name(worker_id))


@pytest.fixture(scope="session")
def test_redis_url(redis_container, worker_id) -> str:
    return get_redis_url(redis_container, _redis_worker_db_index(worker_id))



def _seed_system_model_pricing() -> None:
    """Give the system default model a price, so cost assertions have one.

    Cost is only computed for models present in
    ``LEMMA_SYSTEM_MODEL_METADATA_JSON``; the table is otherwise empty, so
    ``cost_usd`` comes back None and any test asserting on it fails for a
    reason that has nothing to do with what it is testing. That is an ambient
    dependency on a deployment setting, and it made two usage e2e tests pass or
    fail depending on whose machine they ran on.

    Set here rather than in a test because the recording happens in the worker
    SUBPROCESS, which reads this at module import — a monkeypatch inside the
    test would be both too late and in the wrong process. Never overrides a
    real value, so a deployment-shaped run keeps its own pricing.
    """
    if os.environ.get("LEMMA_SYSTEM_MODEL_METADATA_JSON"):
        return
    from app.core.config import settings

    # Every configured model, not just the default: one test deliberately runs
    # a NON-default one, to prove a model without a price entry cannot slip past
    # the usage limits by having its record dropped.
    names = os.environ.get("LEMMA_OPENAI_MODEL_NAMES") or (
        settings.lemma_openai_model_names or ""
    )
    default = os.environ.get("LEMMA_OPENAI_DEFAULT_MODEL") or (
        settings.lemma_openai_default_model
    )
    models = {name.strip() for name in names.split(",") if name.strip()}
    if default:
        models.add(default)
    if not models:
        return
    # Arbitrary non-zero rates. The assertions are "a cost was computed", not
    # "this many dollars", and a real price here would be a lie that drifts.
    os.environ["LEMMA_SYSTEM_MODEL_METADATA_JSON"] = json.dumps(
        {
            model: {
                "input_per_million_usd": 0.1,
                "output_per_million_usd": 0.4,
            }
            for model in sorted(models)
        }
    )

@pytest.fixture(scope="session")
def e2e_settings(test_database_url, test_redis_url, supertokens_container, worker_id):
    from app.core.config import settings

    os.environ["SUPERTOKENS_ENV"] = "testing"
    settings.database_url = test_database_url
    base_url = test_database_url.rsplit("/", 1)[0]
    settings.datastore_database_url = (
        f"{base_url}/{_postgres_worker_datastore_db_name(worker_id)}"
    )
    settings.redis_url = test_redis_url
    settings.supertokens_core_url = get_supertokens_url(supertokens_container)
    settings.environment = "testing"
    settings.debug = True
    # ``api_docs_served()`` is opt-in now (off unless something turns it on) --
    # it used to default to "everywhere except production", which is what kept
    # ``/openapi.json`` reachable here. e2e tests read the live schema to catch
    # route/response drift (e.g. TestAgentOpenApi, the agent_surfaces schema
    # assertions), so opt the e2e stack in explicitly, the same way `make init`
    # sets ``API_DOCS_ENABLED=true`` for the dev stack.
    settings.api_docs_enabled = True
    settings.google_client_id = "test-google-client-id"
    settings.google_client_secret = "test-google-client-secret"
    settings.email_transport = "filesystem"
    settings.auth_email_verification_required = True
    settings.auth_email_deliverability_checks_enabled = False
    settings.auth_abuse_protection_enabled = False
    settings.auth_altcha_enabled = False
    # Namespace local filesystem roots per pytest-xdist worker so parallel
    # workers never share (or rmtree out from under each other) the same dirs.
    # ``PYTEST_XDIST_WORKER`` is e.g. "gw0"/"gw1" under xdist, unset otherwise.
    worker_suffix = _xdist_worker_suffix()
    settings.email_output_dir = f"/tmp/lemma-test-emails{worker_suffix}"
    shutil.rmtree(settings.email_output_dir, ignore_errors=True)
    Path(settings.email_output_dir).mkdir(parents=True, exist_ok=True)
    settings.local_file_storage_root = f"/tmp/lemma-files-tests{worker_suffix}"
    settings.storage_bucket = None
    settings.public_bucket_name = None
    settings.storage_backend = "local"
    settings.embedding_provider = "local"
    settings.local_object_storage_root = (
        f"/tmp/lemma-object-storage-tests{worker_suffix}"
    )

    # Pin the callback server to one session-wide port. Queued functions are
    # dispatched by the session-scoped worker, whose settings load once when its
    # subprocess starts, so a port that changed per test would leave it pointing
    # at a dead one. The function-scoped backend server rebinds this port for
    # each test, so both API- and worker-driven sandboxes get the same explicit,
    # reachable URL. Production code intentionally performs no localhost or
    # container-hostname rewriting.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        callback_port = int(sock.getsockname()[1])
    callback_url = os.getenv(
        "WORKSPACE_E2E_DOCKER_API_URL",
        f"http://host.docker.internal:{callback_port}",
    )
    settings.workspace_callback_api_url = callback_url
    settings.function_runtime_gateway_url = callback_url
    os.environ["WORKSPACE_E2E_BACKEND_PORT"] = str(callback_port)
    os.environ["WORKSPACE_CALLBACK_API_URL"] = callback_url
    os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = callback_url

    # E2E execution mode: default to the fast mocked level (no real model, no
    # Docker) so CI and local runs are fast and deterministic. ``E2E_REAL=1``
    # (or the per-axis E2E_LLM_MODE / E2E_SANDBOX_MODE) opts into the real model
    # + Docker sandbox. Set on os.environ too so the worker subprocess (which
    # inherits os.environ) runs in the same mode.
    real = os.environ.get("E2E_REAL", "").lower() in ("1", "true", "yes")
    llm_mode = os.environ.get("E2E_LLM_MODE") or ("real" if real else "mock")
    sandbox_mode = os.environ.get("E2E_SANDBOX_MODE") or "docker"
    settings.e2e_llm_mode = llm_mode
    settings.e2e_sandbox_mode = sandbox_mode
    os.environ["E2E_LLM_MODE"] = llm_mode
    os.environ["E2E_SANDBOX_MODE"] = sandbox_mode
    _seed_system_model_pricing()
    if llm_mode == "mock":
        # system:lemma normally requires an operator credential and model
        # catalog. The deterministic FunctionModel never contacts that
        # provider, but profile resolution still exercises the production path.
        os.environ.setdefault("LEMMA_OPENAI_API_KEY", "e2e-mock-key-not-used")
        os.environ.setdefault("LEMMA_OPENAI_DEFAULT_MODEL", "e2e-mock-model")
        os.environ.setdefault("LEMMA_OPENAI_MODEL_NAMES", "e2e-mock-model")
        os.environ.setdefault(
            "LEMMA_SYSTEM_MODEL_METADATA_JSON",
            json.dumps(
                {
                    "e2e-mock-model": {
                        "input_per_million_usd": 1.0,
                        "output_per_million_usd": 1.0,
                        "max_input_tokens": 100_000,
                        "max_output_tokens": 20_000,
                        "max_requests": 200,
                    }
                }
            ),
        )

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

    # The schedule poller is a real background loop in the worker subprocess,
    # defaulting to a 5s production cadence. Every schedule/wait-until test
    # that goes through the HTTP API + poller (rather than calling the claim
    # function directly, as test_due_schedule_claimer_e2e.py does) pays that
    # full cadence. Setting the attribute alone does nothing for the worker --
    # it re-reads its own config from its own environment at startup, not
    # this process's settings singleton -- so set os.environ too, same as
    # E2E_LLM_MODE/E2E_DISABLE_WORKER_FILE_AUTOINDEX above; the worker's
    # env={**os.environ, ...} already inherits it, no extra Popen key needed.
    settings.schedule_poll_interval_seconds = 0.5
    os.environ["SCHEDULE_POLL_INTERVAL_SECONDS"] = "0.5"

    from app.core.infrastructure.db import session as db_session_module

    db_session_module.reset_engine_state()

    return settings


@pytest_asyncio.fixture(scope="session", autouse=True)
async def cleanup_workspace_containers_session():
    yield
    # The message bus is a process-wide singleton with no per-test subscribe
    # state (it's publish-only; the real FastStream consumers run in the
    # separate streaq worker subprocess, unaffected by this connection's
    # lifecycle) -- close it once per xdist worker here instead of
    # reconnecting it on every single test's teardown via
    # _close_e2e_process_clients.
    from app.core.infrastructure.events.message_bus import close_message_bus

    await _run_cleanup_step("close_message_bus", close_message_bus)
    # Close exactly the contexts created by this pytest process. Broad sweeps
    # by the shared label are unsafe even in a serial session because another
    # independently invoked pytest process may be running at the same time.
    _close_shared_contexts()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def cleanup_workspace_containers_function():
    yield
    # Per-test: only reap this test's sandbox pods. Sweeping all lemma.e2e
    # containers here would kill the shared session testcontainers and break every
    # subsequent test.
    _cleanup_e2e_workspace_containers(sandboxes_only=True)
    await _close_e2e_process_clients()


def _import_e2e_models() -> None:
    """Populate shared SQLAlchemy metadata before schema creation."""
    from app.core.infrastructure.events import models as event_models
    from app.modules.agent.infrastructure import models as agent_models
    from app.modules.agent_surfaces.infrastructure import models as agent_surface_models
    from app.modules.apps.infrastructure import models as app_models
    from app.modules.connectors.infrastructure import models as connector_models
    from app.modules.datastore.infrastructure.models import datastore_models
    from app.modules.function.infrastructure import models as function_models
    from app.modules.identity.infrastructure.models import (
        organization_models,
        user_models,
    )
    from app.modules.pod.infrastructure import models as pod_role_models
    from app.modules.pod.infrastructure.models import pod_models
    from app.modules.pod_bundle.infrastructure import models as pod_bundle_models
    from app.modules.schedule.infrastructure import models as schedule_models
    from app.modules.usage.infrastructure import models as usage_models
    from app.modules.workflow.infrastructure import models as workflow_models
    from app.modules.workspace.infrastructure import models as workspace_models

    _ = (
        workspace_models,
        event_models,
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
        pod_bundle_models,
    )


@pytest_asyncio.fixture(scope="session")
async def sandbox_reachable_backend(e2e_settings):
    """A backend URL the *live* provisioner's sandboxes can actually reach.

    `e2e_settings` pins the session-wide gateway to `host.docker.internal`,
    which is right for a sandbox on this machine and unresolvable for one in
    E2B's cloud. Queued functions are dispatched by the session-scoped worker,
    which captures its environment once at spawn, so the per-test tunnel in
    `configure_workspace_api_url` comes far too late for it -- which is exactly
    why every JOB function test failed on E2B while the API ones passed.

    Session-scoped for the same reason the port beneath it is: one tunnel, held
    for the whole run, pointed at the port each test's backend rebinds.
    """

    from app.core.config import settings
    from app.modules.test_support.e2e.runtime import _temporary_workspace_tunnel

    off_box = workspace_settings.provider.lower() == "e2b"
    if not off_box:
        yield None
        return

    port = os.environ["WORKSPACE_E2E_BACKEND_PORT"]
    previous = {
        key: os.environ.get(key)
        for key in ("WORKSPACE_CALLBACK_API_URL", "FUNCTION_RUNTIME_GATEWAY_URL")
    }
    original_callback = settings.workspace_callback_api_url
    original_gateway = settings.function_runtime_gateway_url

    async with _temporary_workspace_tunnel(
        f"http://127.0.0.1:{port}", wait_for_backend=False
    ) as public_url:
        settings.workspace_callback_api_url = public_url
        settings.function_runtime_gateway_url = public_url
        os.environ["WORKSPACE_CALLBACK_API_URL"] = public_url
        os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = public_url
        try:
            yield public_url
        finally:
            settings.workspace_callback_api_url = original_callback
            settings.function_runtime_gateway_url = original_gateway
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@pytest_asyncio.fixture(scope="session")
async def worker(e2e_settings, sandbox_reachable_backend):
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

    # Worker lifespans may reconcile persisted state before any function-scoped
    # db_manager fixture runs. Build the schema once before starting the
    # session-scoped production worker; per-test db_manager still truncates it.
    _ensure_repo_root_on_path()
    _import_e2e_models()
    schema_manager = DatabaseManager(e2e_settings.database_url)
    async with schema_manager.engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await schema_manager.create_tables()
    await schema_manager.close()

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
        from app.modules.test_support.e2e.runtime import workspace_provisioning_env

        proc = subprocess.Popen(
            [
                str(backend_root / ".venv/bin/python"),
                "-m",
                "app.worker",
            ],
            cwd=str(backend_root),
            env={
                **os.environ,
                **system_lemma_env_overlay(),  # LEMMA_OPENAI_* from .env
                # The worker provisions its own sandboxes now, so it needs the
                # provider configuration at spawn -- see
                # workspace_provisioning_env().
                **workspace_provisioning_env(),
                # Prepend rather than replace: overwriting it silently drops an
                # inherited PYTHONPATH, so a sibling package resolved from
                # somewhere else (a git worktree checked out beside the venv)
                # gets tested instead of the one under test, and the suite
                # passes or fails for reasons that have nothing to do with the
                # change.
                "PYTHONPATH": os.pathsep.join(
                    part for part in (".", os.environ.get("PYTHONPATH")) if part
                ),
                "DATABASE_URL": e2e_settings.database_url,
                "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
                "REDIS_URL": e2e_settings.redis_url,
                "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
                "WORKSPACE_CALLBACK_API_URL": (
                    e2e_settings.workspace_callback_api_url
                ),
                "FUNCTION_RUNTIME_GATEWAY_URL": (
                    e2e_settings.function_runtime_gateway_url
                ),
                # The manager rebinds to this stable port each test; keep the
                # worker pointed at it so worker-driven function jobs reach it.
                "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
                "ENVIRONMENT": "testing",
                "DEBUG": "true",
                "EMAIL_TRANSPORT": "filesystem",
                "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
                "GCS_STORAGE_BUCKET": "",
                "STORAGE_BUCKET": "",
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
            '"logger": "app.core.infrastructure.jobs.streaq_runtime"',
            '"event": "service.started"',
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
            redis_client = redis.from_url(
                e2e_settings.redis_url, decode_responses=False
            )
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

    _import_e2e_models()

    import asyncio as _asyncio

    from sqlalchemy.exc import DBAPIError

    # The shared session worker runs agent/datastore transactions concurrently
    # with this per-test setup; its row writes can deadlock the truncation DELETE
    # (or the advisory-locked CREATE EXTENSION), and Postgres aborts one side as
    # the victim. Under parallel load Postgres may also drop a pooled connection
    # ("connection was closed in the middle of operation"). Both are transient —
    # retry the whole setup (a dropped connection is replaced via pool_pre_ping on
    # the next attempt) instead of failing a random test's setup each run.
    def _is_transient_db_error(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "deadlock",
                "lock",
                # connection dropped mid-operation / reset under load
                "connection was closed",
                "connection is closed",
                "connectiondoesnotexist",
                "connection reset",
                "server closed the connection",
                "the connection is closed",
            )
        )

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
        except (DBAPIError, OSError) as exc:
            if not _is_transient_db_error(exc):
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
        try:
            yield client
        finally:
            await _close_e2e_process_clients()


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

    await verify_emailpassword_for_tests(data["user"]["id"], email)
    response = await async_client.post("/st/auth/signin", json=signup_data)
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
    """An unsaved ``PodEntity``.

    Kept in sync with the entity by hand: it drifted to constructing ``slug``,
    ``status`` and ``type`` -- and importing ``PodStatus``/``PodType``, which no
    longer exist -- so every test requesting it failed at collection with an
    ImportError rather than an assertion. Nothing referenced it at the time, so
    the breakage was invisible.

    Note this is a detached entity, never persisted. A test that needs a pod the
    authorization layer will recognise has to create one through the API so the
    membership and role rows exist.
    """
    from app.modules.pod.domain.pod_entities import PodEntity

    return PodEntity(
        name=f"Test Pod {uuid4().hex[:8]}",
        description="A test pod",
        user_id=uuid4(),
        organization_id=uuid4(),
    )
