"""Shared E2E fixtures for module-local test conftest files."""

from __future__ import annotations

from app.modules.identity.config import identity_settings
from app.modules.agent.config import agent_settings
from app.modules.datastore.config import datastore_settings
from app.modules.function.config import function_settings
from app.modules.workspace.config import workspace_settings

import os
import threading
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator, Any, Callable, Iterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.infrastructure.db.base import Base
from app.core.infrastructure.db.manager import DatabaseManager, TableSnapshot
from app.modules.test_support.lock_holders import (
    describe_lock_holders,
    warn_lock_holders,
)
from app.core.test_utils import (
    OWNER_LABEL_KEY,
    SHARED_E2E_NETWORK_NAME,
    invocation_owner,
    create_postgres_database,
    get_postgres_uri_from_another_container,
    get_postgres_url,
    get_redis_url,
    get_supertokens_container,
    get_supertokens_url,
)
from app.core.test_shared_containers import (
    E2E_REUSE_ENV,
    drop_set_aside_databases,
    e2e_reuse_requested,
    recreate_postgres_database,
    reused_container_name,
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


def _remove_workspace_volumes() -> None:
    """Remove sandbox workspace volumes left by e2e runs.

    Scoped to this harness's own volumes, by *both* ``managed-by`` and
    ``lemma-owner``. The first alone is not a scope: every Lemma sandbox on
    the machine carries it, including a dev stack's. This function used to
    filter on it and say it was safe because it avoided a broad dangling
    prune -- and it deleted a live dev stack's workspace volume out from under
    somebody, over and over, while an e2e suite ran beside it.

    Filtering on the owner instead is what makes the claim true, and the owner
    is this run's: another run's volumes are its own business until it is
    gone (`sweep_gone_owners`).
    """
    listed = subprocess.run(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=managed-by=lemma-workspace",
            "--filter",
            f"label=lemma-owner={E2E_OWNER_TAG}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    # Each workspace volume's runtime-overlay volume, which carries no labels
    # to filter on and is named after it (`naming.runtime_volume_name`).
    names += [f"{name}-runtime" for name in names]
    if names:
        # A volume still mounted by a container Docker has not finished removing
        # refuses deletion; the next sweep gets it.
        subprocess.run(
            ["docker", "volume", "rm", "-f", *sorted(set(names))],
            check=False,
            capture_output=True,
        )


#: Stamped on every sandbox this process creates (`settings.workspace_owner_tag`,
#: read by the Docker provider) and the only thing its sweeps delete by: the
#: invocation's pid, then the xdist worker, so a test's teardown removes its
#: own worker's sandboxes and nothing else.
#:
#: It used to be the fixed ``lemma-e2e``, naming the harness rather than the
#: run, so two runs on one daemon deleted each other's live sandboxes on every
#: teardown. An interrupted run's leftovers are still cleaned up: by
#: `sweep_gone_owners` once its process is gone, never while it is alive.
E2E_OWNER_TAG = (
    f"{invocation_owner()}-{os.environ.get('PYTEST_XDIST_WORKER', 'master')}"
)


def sweep_filter_sets(*, sandboxes_only: bool) -> list[list[str]]:
    """The `docker ps` filters this process is allowed to delete by.

    Its own function so the property that matters can be asserted without a
    Docker daemon: **every set must name this run's owner.** Docker's filters
    are conjunctive, so each set is one owner-scoped selection.

    `managed-by=lemma-workspace` alone matches every Lemma sandbox on the
    daemon -- a developer's dev stack's included, which an e2e run once deleted
    over and over while somebody typed into it. `lemma.e2e=true` alone matches
    every e2e run's containers, which is how parallel runs killed each other's
    Postgres. Both are only ever used paired with an owner.
    """
    workspace_ours = [
        "--filter",
        "label=managed-by=lemma-workspace",
        "--filter",
        f"label=lemma-owner={E2E_OWNER_TAG}",
    ]
    if sandboxes_only:
        return [workspace_ours]
    return [
        [
            "--filter",
            "label=lemma.e2e=true",
            "--filter",
            f"label={OWNER_LABEL_KEY}={invocation_owner()}",
        ],
        workspace_ours,
    ]


def _cleanup_e2e_workspace_containers(*, sandboxes_only: bool = False) -> None:
    """Remove this run's leftover Docker containers -- never another run's.

    - ``sandboxes_only=True`` (per-test cleanup): this worker's sandboxes only,
      sparing the shared session containers -- otherwise the workspace teardown
      after the first test kills postgres/redis and every later test fails to
      connect.
    - default: this invocation's session containers as well.

    Another run's containers, live or not, are never matched here; an
    interrupted run's are `sweep_gone_owners`'s, once its process is gone.
    """
    if not shutil.which("docker"):
        return

    filter_sets = sweep_filter_sets(sandboxes_only=sandboxes_only)

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

    _remove_workspace_volumes()

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
    production without invoking their production shutdown hooks.

    Call it only through the ``e2e_process_clients`` fixture, which exists to
    put it after every fixture that could still be using one of these -- see
    that fixture for what goes wrong when it runs earlier.
    """

    from app.core.infrastructure.cache.redis_json_cache import close_redis_json_caches
    from app.core.infrastructure.channels.channel_service import channel_service
    from app.core.infrastructure.db.session import close_engine
    from app.modules.agent_surfaces.infrastructure.adapters.stranger_reply_window import (
        close_stranger_reply_window,
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
    await _run_cleanup_step("close_stranger_reply_window", close_stranger_reply_window)
    await _run_cleanup_step("close_user_cache", close_user_cache)
    await _run_cleanup_step("close_auth_abuse_store", close_auth_abuse_store)
    await _run_cleanup_step("close_telegram_oidc_store", close_telegram_oidc_store)
    await _run_cleanup_step("close_redis_json_caches", close_redis_json_caches)
    await _run_cleanup_step("channel_service.disconnect", channel_service.disconnect)
    await _run_cleanup_step("close_datastore_engine", close_datastore_engine)
    if not _primary_engine_is_reusable():
        await _run_cleanup_step("close_engine", close_engine)


def _primary_engine_is_reusable() -> bool:
    """Whether the next test can keep this process's primary engine.

    Disposing it after every test made each test rebuild what an engine
    keeps: the dialect's first-connect setup, the compiled-statement cache,
    and -- the e2e process pools (`DB_POOL_IN_TESTING`) -- its connections
    with their prepared statements. About a sixth of a second a test. Kept
    only while it is still the engine `get_engine` would build: same database
    and same pooling. Between tests its connections are idle in the pool and
    hold no locks, so the next test's reset is not waiting on them.
    """
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.core.infrastructure.db import session

    current = session.engine
    if current is None:
        return True
    unpooled = isinstance(current.pool, NullPool)
    return unpooled == (not settings.db_pool_in_testing) and (
        current.url.render_as_string(hide_password=False) == settings.database_url
    )


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


def _supertokens_singleton_classes() -> tuple[type, ...]:
    """Every SDK class that keeps process-wide state between ``init`` calls."""
    from supertokens_python.querier import Querier
    from supertokens_python.recipe.accountlinking.recipe import AccountLinkingRecipe
    from supertokens_python.recipe.dashboard.recipe import DashboardRecipe
    from supertokens_python.recipe.emailpassword.recipe import EmailPasswordRecipe
    from supertokens_python.recipe.passwordless.recipe import PasswordlessRecipe
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

    return (
        Supertokens,
        Querier,
        SessionRecipe,
        AccountLinkingRecipe,
        EmailPasswordRecipe,
        PasswordlessRecipe,
        EmailVerificationRecipe,
        DashboardRecipe,
        ThirdPartyRecipe,
        JWTRecipe,
        OpenIdRecipe,
        MultitenancyRecipe,
        UserMetadataRecipe,
        OAuth2ProviderRecipe,
    )


def _reset_supertokens_testing_state() -> None:
    from supertokens_python.querier import Querier
    from supertokens_python.supertokens import Supertokens

    Supertokens.reset()
    for recipe in _supertokens_singleton_classes():
        if recipe not in (Supertokens, Querier):
            recipe.reset()


#: Class state the SDK keeps under plain names: ``Querier.init`` installs a
#: ``network_interceptor``, and the first request caches the core's
#: ``api_version``. A test that initialises with its own leaves both behind.
_SUPERTOKENS_PUBLIC_CLASS_STATE = frozenset({"network_interceptor", "api_version"})


@contextmanager
def supertokens_state_restored() -> Iterator[None]:
    """Reset SuperTokens for the body, then put back what was there before.

    Importing ``app.app`` initialises the SDK once for the whole process, and
    unit tests that drive the app without its lifespan rely on that. A test
    that resets the SDK and leaves it reset turns every later request in the
    same worker into "Initialisation not done" -- a failure that depends on
    which tests xdist happened to put together. Restoring the private class
    attributes (the singletons and the querier's host list) is the only way
    back, because the SDK has no public "re-install this instance" call.
    """
    snapshot = [
        (
            cls,
            {
                k: v
                for k, v in vars(cls).items()
                if k.startswith(f"_{cls.__name__}__")
                or k in _SUPERTOKENS_PUBLIC_CLASS_STATE
            },
        )
        for cls in _supertokens_singleton_classes()
    ]
    _reset_supertokens_testing_state()
    try:
        yield
    finally:
        _reset_supertokens_testing_state()
        for cls, attributes in snapshot:
            for name, value in attributes.items():
                setattr(cls, name, value)


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
    # One core and one database per xdist worker. Sharing one core across
    # workers was tried and measured: it saved a JVM start but made every
    # worker-driven test wait on it -- the schedule shard's pod-bundle tests
    # took 257s against 148s with a core each (CI runs 39023294610 and
    # 39023300378).
    return f"supertokens_{_sanitize_worker_id(worker_id)}"


def _start_postgres(worker_id: str, basetemp_parent, *, schema: bool = True) -> None:
    """Connect to the ONE Postgres server shared across all xdist workers.

    Each worker gets its own logical database inside it (created via
    ``create_postgres_database``), rather than its own full container -- one
    `docker run postgres` and one idle Postgres server process for the whole
    run instead of N. See ``shared_postgres`` in test_shared_containers for the
    coordination mechanism (mirrors ``shared_kreuzberg``).
    """

    def _factory():
        return shared_postgres(basetemp_parent, worker_id)

    _claim_reuse(worker_id)
    postgres = _shared_context_resource("postgres", _factory)
    if not getattr(postgres, "_lemma_worker_databases_created", False):
        if e2e_reuse_requested():
            _fresh_reused_databases(postgres, worker_id, schema=schema)
        else:
            create_postgres_database(postgres, _postgres_worker_db_name(worker_id))
            create_postgres_database(
                postgres, _postgres_worker_datastore_db_name(worker_id)
            )
        create_postgres_database(
            postgres, _postgres_worker_supertokens_db_name(worker_id)
        )
        setattr(postgres, "_lemma_worker_databases_created", True)


#: Held for the life of this process when it reuses containers, so a second
#: session in the same checkout cannot recreate the databases under it.
_REUSE_LOCKS: list[Any] = []
_REUSE_CLAIM = threading.Lock()


def _claim_reuse(worker_id: str) -> None:
    """Take this worker's reuse lock, or fall back to fresh containers.

    Reused containers are shared by every session in a checkout, but each
    xdist worker's databases and Redis index are its own -- so two sessions
    at once would wipe each other's. The second one simply runs cold.
    """
    with _REUSE_CLAIM:
        if not e2e_reuse_requested() or _REUSE_LOCKS:
            return
        from filelock import FileLock, Timeout

        # Not thread-local: containers are warmed from several threads of
        # this one process, and all of them hold the same claim.
        lock = FileLock(
            f"/tmp/{reused_container_name(_sanitize_worker_id(worker_id))}.lock",
            thread_local=False,
        )
        try:
            lock.acquire(timeout=0)
        except Timeout:
            os.environ[E2E_REUSE_ENV] = "0"
            return
        _REUSE_LOCKS.append(lock)


def _fresh_reused_databases(postgres: Any, worker_id: str, *, schema: bool) -> None:
    """This session's databases, new; for the full stack, cloned from a schema template.

    Cloning a template is cheaper than creating every table again; tables
    registered after the template was built are still created lazily by
    ``_ensure_schema_once``. A Postgres-only session (``postgres_only_*``)
    creates its own tables, so it skips the template and the model imports
    that building one needs. The previous session's databases are dropped on a
    background thread, off the path to the first test.
    """
    name = _postgres_worker_db_name(worker_id)
    if schema:
        template, tables = _schema_template(postgres)
        recreate_postgres_database(postgres, name, template=template)
        _CREATED_TABLES[get_postgres_url(postgres, name)] = set(tables)
    else:
        recreate_postgres_database(postgres, name)
    recreate_postgres_database(postgres, _postgres_worker_datastore_db_name(worker_id))
    threading.Thread(
        target=drop_set_aside_databases, args=(postgres,), daemon=True
    ).start()


def _schema_template(postgres: Any) -> tuple[str, list[str]]:
    """A database holding the e2e schema, named by a digest of that schema."""
    import hashlib

    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    _import_e2e_models()
    dialect = postgresql.dialect()
    tables = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
    ddl = "".join(
        str(CreateTable(t).compile(dialect=dialect))
        + "".join(str(CreateIndex(i).compile(dialect=dialect)) for i in t.indexes)
        for t in tables
    )
    name = f"lemma_e2e_tpl_{hashlib.sha256(ddl.encode()).hexdigest()[:12]}"
    admin = get_postgres_url(postgres).replace("+asyncpg", "+psycopg")
    with psycopg.connect(
        admin.replace("postgresql+psycopg", "postgresql"), autocommit=True
    ) as conn:
        # One builder at a time across xdist workers; the rest find it built.
        conn.execute("SELECT pg_advisory_lock(%s)", (0x6C656D7470,))
        try:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if exists is None:
                for (stale,) in conn.execute(
                    "SELECT datname FROM pg_database WHERE datname LIKE 'lemma\\_e2e\\_tpl\\_%'"
                ).fetchall():
                    conn.execute(f'DROP DATABASE IF EXISTS "{stale}"')
                building = f"{name}_building"
                conn.execute(f'DROP DATABASE IF EXISTS "{building}"')
                conn.execute(f'CREATE DATABASE "{building}"')
                engine = create_engine(admin.rsplit("/", 1)[0] + f"/{building}")
                try:
                    with engine.begin() as schema:
                        schema.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                        schema.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
                        Base.metadata.create_all(schema, tables=tables)
                finally:
                    engine.dispose()
                conn.execute(f'ALTER DATABASE "{building}" RENAME TO "{name}"')
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (0x6C656D7470,))
    return name, [t.name for t in tables]


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

    reuse_name = (
        reused_container_name(f"supertokens-{_sanitize_worker_id(worker_id)}")
        if e2e_reuse_requested()
        else None
    )

    def _factory():
        return get_supertokens_container(
            postgres_uri, network=SHARED_E2E_NETWORK_NAME, reuse_name=reuse_name
        )

    _shared_context_resource("supertokens", _factory)


def _start_redis(worker_id: str, basetemp_parent) -> None:
    """Connect to the ONE Redis server shared across all xdist workers.

    Each worker gets its own logical DB index inside it (selected via the
    connection URL, see ``_redis_worker_db_index``), rather than its own full
    container -- one `docker run redis` for the whole run instead of N. See
    ``shared_redis`` in test_shared_containers for the coordination mechanism (mirrors
    ``shared_postgres``/``shared_kreuzberg``). Unlike Postgres, no
    per-worker provisioning step is needed here -- Redis DBs exist by index
    already, nothing to create.
    """

    def _factory():
        return shared_redis(basetemp_parent, worker_id)

    _claim_reuse(worker_id)
    first = "redis" not in _SHARED_RESOURCES
    redis_container = _shared_context_resource("redis", _factory)
    if first and e2e_reuse_requested():
        # A reused server keeps the last session's keys; this worker's index
        # starts empty, as it would on a fresh container.
        import redis as redis_sync

        url = get_redis_url(redis_container, _redis_worker_db_index(worker_id))
        with redis_sync.Redis.from_url(url) as client:
            client.flushdb()


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
    if all(name in _SHARED_RESOURCES for name in ("postgres", "redis", "supertokens")):
        return

    # Resolve once on this (the calling) thread before fanning out to the
    # pool below. ``TempPathFactory.getbasetemp()`` lazily creates and caches
    # the base temp dir on first call and is not safe to invoke from two
    # threads at once -- postgres and redis both used to derive this
    # independently inside their own worker thread, and running both jobs
    # concurrently raced two `mkdir`s for the same path, one losing with
    # ``FileExistsError``.
    basetemp_parent = tmp_path_factory.getbasetemp().parent
    _claim_reuse(worker_id)

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
def postgres_only_container(worker_id, tmp_path_factory):
    """The shared Postgres server alone, for tests of the database layer.

    ``postgres_container`` warms SuperTokens and Redis alongside it, which is
    right for a test that boots the app and pure cost -- most of the setup
    time -- for one that only needs a database. Same shared server and
    per-worker database, so a later test asking for the full stack reuses it
    and starts only what is missing.
    """
    _start_postgres(worker_id, tmp_path_factory.getbasetemp().parent, schema=False)
    yield _SHARED_RESOURCES["postgres"]


@pytest.fixture(scope="session")
def postgres_only_database_url(postgres_only_container, worker_id) -> str:
    return get_postgres_url(
        postgres_only_container, _postgres_worker_db_name(worker_id)
    )


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

    # Every configured model, not just the default: one test deliberately runs
    # a NON-default one, to prove a model without a price entry cannot slip past
    # the usage limits by having its record dropped.
    names = os.environ.get("LEMMA_OPENAI_MODEL_NAMES") or (
        agent_settings.lemma_openai_model_names or ""
    )
    default = os.environ.get("LEMMA_OPENAI_DEFAULT_MODEL") or (
        agent_settings.lemma_openai_default_model
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


#: The callback port, picked once per pytest process.
#:
#: "Session-scoped" is per fixture *definition*, not per process: every e2e
#: conftest re-exports `e2e_settings` under its own name, so pytest registers
#: one definition per conftest and runs each once -- `e2e_settings` runs again
#: for every module directory the run enters. It used to pick a fresh port each
#: time, while the session worker keeps the port it was born with, so from the
#: second directory on the worker's sandboxes called back to a port nothing
#: listened on any more.
_CALLBACK_PORT: int | None = None


def _session_callback_port() -> int:
    global _CALLBACK_PORT
    if _CALLBACK_PORT is None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            _CALLBACK_PORT = int(sock.getsockname()[1])
    return _CALLBACK_PORT


@pytest.fixture(scope="session")
def e2e_settings(test_database_url, test_redis_url, supertokens_container, worker_id):
    from app.core.config import settings

    os.environ["SUPERTOKENS_ENV"] = "testing"
    settings.database_url = test_database_url
    base_url = test_database_url.rsplit("/", 1)[0]
    datastore_settings.datastore_database_url = (
        f"{base_url}/{_postgres_worker_datastore_db_name(worker_id)}"
    )
    settings.redis_url = test_redis_url
    settings.supertokens_core_url = get_supertokens_url(supertokens_container)
    settings.environment = "testing"
    # Makes every sandbox this run creates say so, which is what lets the
    # sweeps above delete ours and nothing else. Set on the settings object
    # rather than the environment because the config singleton was built when
    # this module was imported and will not re-read it.
    workspace_settings.owner_tag = E2E_OWNER_TAG
    settings.debug = True
    # ``api_docs_served()`` is opt-in now (off unless something turns it on) --
    # it used to default to "everywhere except production", which is what kept
    # ``/openapi.json`` reachable here. e2e tests read the live schema to catch
    # route/response drift (e.g. TestAgentOpenApi, the agent_surfaces schema
    # assertions), so opt the e2e stack in explicitly, the same way `make init`
    # sets ``API_DOCS_ENABLED=true`` for the dev stack.
    settings.api_docs_enabled = True
    identity_settings.google_client_id = "test-google-client-id"
    identity_settings.google_client_secret = "test-google-client-secret"
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
    callback_port = _session_callback_port()
    callback_url = os.getenv(
        "WORKSPACE_E2E_DOCKER_API_URL",
        f"http://host.docker.internal:{callback_port}",
    )
    workspace_settings.workspace_callback_api_url = callback_url
    function_settings.function_runtime_gateway_url = callback_url
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
    # ...but raising the attempt count without shrinking the base delay turned a
    # blip into a stall. The total wait is base * (2^(attempts-1) - 1), which the
    # datastore config docstring says is "the dominant wait": at the production
    # 1.0s base, eight attempts is 127 seconds, against a 240s test timeout. The
    # per-test document_worker fixture already gets this right for its own fake
    # processor (attempts=2, base=0.01); this is the session-wide equivalent.
    os.environ.setdefault("KREUZBERG_TRANSIENT_RETRY_BASE_DELAY_SECONDS", "0.05")
    # Once five consecutive connection failures open the circuit it stays open
    # for a real 30 seconds before a half-open trial, and any test that follows
    # a wobble in the same process pays it.
    os.environ.setdefault("KREUZBERG_CIRCUIT_RESET_SECONDS", "0.5")

    # e2e indexes datastore files explicitly in-process (the index_file helper).
    # Disable the worker's auto-index-on-upload so it doesn't ALSO index every
    # uploaded file through the single shared Kreuzberg — that double load OOMs
    # the container under -n2. Inherited by the worker subprocess.
    datastore_settings.e2e_disable_worker_file_autoindex = True
    os.environ.setdefault("E2E_DISABLE_WORKER_FILE_AUTOINDEX", "true")

    # The production cadences the suite sits through. Each is a background
    # loop the tests wait on rather than drive, so the production interval is
    # pure latency here.
    # Attribute + os.environ for the same reason as above: the worker subprocess
    # re-reads its own config from its own environment.
    #
    #   agent_run_stop_poll_interval_seconds  1.0s -- every stop/cancel journey
    #   function_run_poll_interval_seconds    0.5s -- every agent-dispatches-a-
    #                                                 function wait
    #   outbox_*                              5.0s -> 0.5s (their declared
    #                                                 floor) -- normally masked by the
    #       pg_notify wake, but it degrades *silently* to the full interval
    #       whenever the listen path is not attached, which reads as "this test
    #       is sometimes five seconds slower" rather than as a fault.
    os.environ["AGENT_RUN_STOP_POLL_INTERVAL_SECONDS"] = "0.1"
    os.environ["FUNCTION_RUN_POLL_INTERVAL_SECONDS"] = "0.1"
    # 0.5 is the floor these two declare (ge=0.5); anything lower fails
    # validation at import and takes the whole app down, not just the setting.
    os.environ["OUTBOX_IDLE_POLL_MAX_SECONDS"] = "0.5"
    os.environ["OUTBOX_LISTEN_FALLBACK_POLL_SECONDS"] = "0.5"
    _shorten_worker_cadences()
    _shorten_live_cadences()
    _shorten_send_backoffs()

    # Pool connections instead of opening a fresh one per unit of work.
    #
    # `db_pool_in_testing` defaults to False, and its docstring justifies that
    # with "the pytest process runs many event loops and a pooled connection
    # must not outlive the loop that opened it". That has not been true since
    # pytest.ini pinned asyncio_default_fixture_loop_scope and
    # asyncio_default_test_loop_scope to `session`: there is exactly one loop.
    #
    # The cost it left behind is measured in that same docstring -- "JOB queue
    # latency was 0.3s under NullPool against 0.06s pooled" -- and this suite is
    # IO-bound, so a full TCP connect per unit of work is close to pure latency.
    # It also means two xdist workers churn connections against one shared
    # Postgres for the whole run.
    settings.db_pool_in_testing = True

    from app.core.infrastructure.db import session as db_session_module

    db_session_module.reset_engine_state()

    return settings


#: Seconds between the session worker's fallback polls. NOTIFY wakes it sooner
#: whenever the wake reaches it; the poll is what a test waits through when it
#: does not, and what notices a delayed item coming due. Production's 1 s is
#: latency every such test pays.
_E2E_WORKER_POLL_SECONDS = 0.2


def _shorten_worker_cadences() -> None:
    """Shorten the work dispatcher's and datastore hand-off's fallback polls.

    Both the settings singletons and the environment: this process's own work
    runtimes (a ``drives_work`` test's dispatcher) read the singletons, which
    were built at import, and the worker subprocess builds its own from the
    environment it inherits.
    """
    from app.core.infrastructure.work.config import work_settings

    work_settings.work_poll_interval_seconds = _E2E_WORKER_POLL_SECONDS
    datastore_settings.datastore_change_handoff_poll_seconds = _E2E_WORKER_POLL_SECONDS
    os.environ["WORK_POLL_INTERVAL_SECONDS"] = str(_E2E_WORKER_POLL_SECONDS)
    os.environ["DATASTORE_CHANGE_HANDOFF_POLL_SECONDS"] = str(_E2E_WORKER_POLL_SECONDS)


def _shorten_live_cadences() -> None:
    """Shorten the live hubs' fallback timers, which run in this process.

    A hub reads its pod's log on a wake, else every idle poll; it compares the
    grants epoch every authorization check. At their production values (5s and
    2s) a test waiting for a revocation, or proving nothing arrived, sat out
    seconds per wait. A missed wake still shows: only later.

    The idle poll stays above the sockets' quiet window: a poll can still offer
    a reader a channel that changed just before they subscribed, and
    ``test_a_change_that_moves_none_of_a_readers_counts_sends_them_nothing``
    would see it.
    """
    from app.core.live.config import live_settings

    live_settings.live_idle_poll_seconds = 2.0
    live_settings.live_authz_check_seconds = 0.2
    live_settings.live_run_access_check_seconds = 0.2


def _shorten_send_backoffs() -> None:
    """Shrink the inline retry backoff of a platform send, here and in the
    worker subprocess (which inherits the environment).

    Tests that make a fake platform fail on purpose otherwise sat out the real
    0.5s-doubling-to-8s waits between attempts; the attempts themselves, and a
    platform's Retry-After, are unchanged.
    """
    from app.modules.agent_surfaces.config import surface_settings

    surface_settings.surface_send_backoff_scale = 0.02
    os.environ["SURFACE_SEND_BACKOFF_SCALE"] = "0.02"


def _import_e2e_models() -> None:
    """Populate shared SQLAlchemy metadata before schema creation.

    Every module that declares a ``__tablename__`` has to appear here, or its
    tables exist in ``Base.metadata`` only once some *test* happens to import
    them -- after the schema was created. ``runtime_models`` was missing, and it
    declares six: agent_hosts, agent_host_pairings, agent_host_harnesses,
    agent_host_commands, agent_host_run_leases and agent_runtime_profiles.

    That made schema creation order-dependent.
    ``test_dynamic_agent_function_tools_e2e`` fails on `relation
    "agent_runtime_profiles" does not exist` when its file runs on its own, and
    passes in CI only because an earlier test in the same shard imports the
    module first. The other modules listed below re-export their submodels
    through a package ``__init__``, so importing the package is enough.
    """
    from app.core.infrastructure.work import models as work_models
    from app.modules.agent.infrastructure import models as agent_models
    from app.modules.agent.infrastructure import runtime_models as agent_runtime_models
    from app.modules.agent_surfaces.infrastructure import models as agent_surface_models
    from app.modules.apps.infrastructure import models as app_models
    from app.modules.connectors.infrastructure import models as connector_models
    from app.modules.contacts.infrastructure import models as contact_models
    from app.modules.channels.infrastructure import models as channel_models
    from app.modules.contacts.infrastructure import (
        visitor_sessions as visitor_session_models,
    )
    from app.modules.agent_surfaces.infrastructure import web_widget_models
    from app.modules.datastore.infrastructure.models import datastore_models
    from app.modules.function.infrastructure import models as function_models
    from app.modules.mcp_access.infrastructure import models as mcp_access_models
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
        agent_runtime_models,
        work_models,
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
        mcp_access_models,
        contact_models,
        channel_models,
        visitor_session_models,
        web_widget_models,
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
    original_callback = workspace_settings.workspace_callback_api_url
    original_gateway = function_settings.function_runtime_gateway_url

    async with _temporary_workspace_tunnel(
        f"http://127.0.0.1:{port}", wait_for_backend=False
    ) as public_url:
        workspace_settings.workspace_callback_api_url = public_url
        function_settings.function_runtime_gateway_url = public_url
        os.environ["WORKSPACE_CALLBACK_API_URL"] = public_url
        os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = public_url
        try:
            yield public_url
        finally:
            workspace_settings.workspace_callback_api_url = original_callback
            function_settings.function_runtime_gateway_url = original_gateway
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


#: The session worker subprocess of this pytest process, by database URL.
_SESSION_WORKERS: dict[str, subprocess.Popen] = {}


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

    from app.core.config import settings

    # One worker per process, however many conftests re-export this fixture.
    # Each re-export is its own session-scoped definition, so without this
    # every module directory started another worker and none of them stopped
    # until the run ended: by the function tests a dozen were claiming from
    # the same work table, and whichever won a job ran it with the settings it
    # was born with -- see `_session_callback_port`.
    shared = _SESSION_WORKERS.get(e2e_settings.database_url)
    if shared is not None and shared.poll() is None:
        yield shared
        return

    # Worker lifespans may reconcile persisted state before any function-scoped
    # db_manager fixture runs. Build the schema once before starting the
    # session-scoped production worker; per-test db_manager still truncates it.
    _ensure_repo_root_on_path()
    _import_e2e_models()
    await _ensure_schema_once(e2e_settings.database_url)

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
                "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url,
                # A pool, as in production: under the testing default of
                # NullPool every unit of work the worker runs opens a new
                # Postgres connection, which is most of a job's latency on a
                # CI runner. One loop, one process: nothing outlives its loop.
                "DB_POOL_IN_TESTING": "true",
                "DB_POOL_SIZE": "5",
                "REDIS_URL": e2e_settings.redis_url,
                "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
                "WORKSPACE_CALLBACK_API_URL": (
                    workspace_settings.workspace_callback_api_url
                ),
                # `function_settings`, not `e2e_settings`: this field moved to
                # `FunctionSettings`, and `e2e_settings` is core's. The two are
                # separate objects, so reading it off the wrong one is an
                # `AttributeError` in a fixture -- which the unit lane cannot
                # see, because only a worker subprocess reaches this.
                "FUNCTION_RUNTIME_GATEWAY_URL": (
                    function_settings.function_runtime_gateway_url
                ),
                # The manager rebinds to this stable port each test; keep the
                # worker pointed at it so worker-driven function jobs reach it.
                "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
                "ENVIRONMENT": "testing",
                # E2E workers have no production drain to protect, and the default
                # 10s grace period equalled the teardown's patience -- so the worker
                # spent its whole grace draining, overran, and got SIGKILLed. SIGKILL
                # cannot be trapped, so coverage's `sigterm = true` handler never
                # flushed and the subprocess's coverage was lost.
                "WORK_SHUTDOWN_GRACE_SECONDS": "1",
                "WORK_SHUTDOWN_GRACE_JSON": '{"interactive": 1}',
                "DEBUG": "true",
                "EMAIL_TRANSPORT": "filesystem",
                # `settings`, not `e2e_settings`: they are the same object --
                # the fixture mutates the core singleton and hands it back --
                # but `check_settings_attrs.py` can only resolve a name it can
                # follow to an import. A field read off a fixture parameter is
                # invisible to it, which is how the harness set these by string
                # key and nothing noticed until a sandbox was already running.
                # The parameter stays in the signature: it is what orders this
                # after the fixture has applied its overrides.
                "EMAIL_OUTPUT_DIR": settings.email_output_dir,
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
            '"logger": "app.core.infrastructure.work.process"',
            '"event": "service.started"',
        )
        startup_ok = False
        # The worker reports started only after it has loaded the local
        # embedding model, and on a cold cache that includes downloading it
        # from the Hugging Face Hub. A budget shorter than that download lost
        # the race on CI whenever the fetch was slow, and every test in the run
        # failed at setup. A few minutes covers a cold download, and a hung
        # start still fails.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 180
        while loop.time() < deadline:
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

        from app.modules.test_support.e2e.worker_hold import (
            register_session_worker,
            unregister_session_worker,
        )

        register_session_worker(proc.pid, e2e_settings.database_url)
        _SESSION_WORKERS[e2e_settings.database_url] = proc
        try:
            yield proc
        finally:
            _SESSION_WORKERS.pop(e2e_settings.database_url, None)
            unregister_session_worker(proc.pid)
            proc.terminate()
            try:
                # Comfortably longer than the 1s grace period set at spawn, so
                # SIGKILL becomes unreachable in practice.
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            redis_client = redis.from_url(
                e2e_settings.redis_url, decode_responses=False
            )
            await redis_client.flushdb()
            await redis_client.aclose()


# Table names this process has already created, per database.
#
# The per-test `create_all(checkfirst=True)` this replaces read as cheap -- the
# comment on db_manager used to call it exactly that -- but SQLAlchemy's
# PostgreSQL dialect issues one `has_table` round trip per table on this path
# and does not cache them, so it was 55 statements per test to discover that
# almost nothing had changed. Measured on test_records_e2e.py: 54.7 reflection
# statements per test, a quarter of all setup SQL.
#
# "Almost" is the important word, and it is why this tracks table *names* rather
# than just "have we run yet". Models register with Base.metadata lazily as the
# app imports them, so metadata genuinely grows between tests -- the first naive
# version of this hoisted create_all to run once and 20 of 21 tests then failed
# on `relation "agent_host_run_leases" does not exist`. Creating only the tables
# that appeared since last time keeps that correctness and still costs zero
# round trips on the common path, where nothing appeared.
#
# Keyed by URL because each xdist worker gets its own logical database, and each
# xdist worker is its own process with its own copy of this dict.
_CREATED_TABLES: dict[str, set[str]] = {}


async def _ensure_schema_once(database_url: str) -> None:
    """Create the extension once, and any tables not yet created."""
    known = _CREATED_TABLES.setdefault(database_url, set())
    pending = [
        table for name, table in Base.metadata.tables.items() if name not in known
    ]
    if not pending:
        return

    manager = DatabaseManager(database_url)
    try:
        if not known:
            async with manager.engine.begin() as conn:
                # Serialize with PostgresSearchService.ensure_schema(), which
                # also runs CREATE EXTENSION under this advisory key (concurrent
                # CREATE EXTENSION on pg_extension otherwise deadlocks). Key must
                # match _ENSURE_SCHEMA_LOCK_KEY in postgres_search_service.
                await conn.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x6C656D6D61}
                )
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
        async with manager.engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all, tables=pending, checkfirst=True
            )
    finally:
        await manager.close()
    known.update(table.name for table in pending)


#: Where a module's shared identity rows are kept between its tests. One per
#: database: modules run one after another in an xdist worker, never together.
_IDENTITY_SNAPSHOT_SCHEMA = "e2e_module_identity"

#: Queue tables never restored from a snapshot: a work item copied back for
#: every test would be run again for every test, and a rate bucket would carry
#: one test's spent budget into the next. ``work_queues`` is restored: it is
#: each namespace's configuration, which a test starting from a fresh pod had
#: already. Without it a pod's first concurrent writes each open a side
#: connection to create the row, and nineteen of them on a ten-connection
#: pool time out (`test_lines_written_at_once_are_numbered_without_gaps`).
_SNAPSHOT_EXCLUDED_TABLES = ("work_items", "work_rate_buckets")


@dataclass(frozen=True, slots=True)
class _IdentitySnapshot:
    rows: TableSnapshot
    #: Ids of the shared user and organization, as they appear in cache keys.
    ids: tuple[str, ...]


#: The snapshot of the module identity currently set up, per database.
_IDENTITY_SNAPSHOTS: dict[str, _IdentitySnapshot] = {}


async def _forget_cached_identity(redis_url: str, ids: tuple[str, ...]) -> None:
    """Drop every Redis key naming the shared user or organization.

    A test that changes, say, the user's role in the organization leaves a
    cached role behind, and the row reset does not reach Redis. With a new
    identity per test no later test could ever look that key up; with a
    shared one the next test would read it. Only those keys go: the session
    worker shares this Redis index and keeps state of its own in it.
    """
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=False)
    # One pass over the keyspace for every id, not one per id: a module that
    # extends its identity names a dozen of them, and this runs before every
    # test.
    needles = tuple(identity_id.encode() for identity_id in ids)
    try:
        stale = [
            key
            async for key in client.scan_iter(count=1000)
            if any(needle in key for needle in needles)
        ]
        if stale:
            await client.delete(*stale)
    finally:
        await client.aclose()


#: One manager per database for the life of the test process. A manager per
#: test paid for a new engine each time -- connections, dialect setup and the
#: compiled-statement cache -- in every test's setup, for an engine whose
#: configuration never changes. Its connections sit idle in a two-slot pool
#: between tests. The process exiting closes them.
_DB_MANAGERS: dict[str, DatabaseManager] = {}


def _shared_db_manager(database_url: str) -> DatabaseManager:
    manager = _DB_MANAGERS.get(database_url)
    if manager is None:
        manager = _DB_MANAGERS[database_url] = DatabaseManager(database_url)
    return manager


async def _truncate_retrying_deadlocks(
    manager: DatabaseManager, *, restore: TableSnapshot | None = None
) -> None:
    """A module's reset, retried when Postgres picks it as a deadlock victim.

    The shared worker can still be finishing the previous module's writes
    when the next module empties the tables; the per-test reset in
    ``db_manager`` already retries this, and a module's reset met it once in
    every few runs (``test_export_pod_bundle_roundtrip``'s setup)."""
    from sqlalchemy.exc import DBAPIError

    for attempt in range(6):
        try:
            await manager.truncate_all(restore=restore)
            return
        except DBAPIError as exc:
            if "deadlock" not in str(exc).lower() or attempt == 5:
                raise
            await asyncio.sleep(0.3 * (attempt + 1))


@pytest_asyncio.fixture(scope="function")
async def db_manager(e2e_settings, request) -> AsyncGenerator[DatabaseManager, None]:
    # Per-test data isolation only. The schema itself is prepared once per
    # database (_ensure_schema_once) and persists for the whole run, so each test
    # pays a filtered DELETE sweep rather than a drop/create. Keeping the schema
    # stable also lets the shared streaq worker hold its connections.
    _ensure_repo_root_on_path()
    manager = _shared_db_manager(e2e_settings.database_url)

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

    # A test that shares its module's identity starts from that identity's
    # rows and nothing else; every other test starts from empty tables.
    identity = (
        _IDENTITY_SNAPSHOTS.get(e2e_settings.database_url)
        if "module_identity" in request.fixturenames
        else None
    )
    restore = identity.rows if identity is not None else None
    last_exc: BaseException | None = None
    for _attempt in range(6):
        try:
            # Once per database, not once per test — see _ensure_schema_once.
            await _ensure_schema_once(e2e_settings.database_url)
            # Start each test from a clean slate without dropping the schema.
            await manager.truncate_all(restore=restore)
            if identity is not None:
                await _forget_cached_identity(e2e_settings.redis_url, identity.ids)
            break
        except (DBAPIError, OSError) as exc:
            if not _is_transient_db_error(exc):
                raise
            last_exc = exc
            if "lock timeout" in str(exc).lower():
                # A wait that later succeeds still costs the test its duration
                # budget; the warnings summary names the holder either way.
                warn_lock_holders(
                    await describe_lock_holders(e2e_settings.database_url)
                )
            await _asyncio.sleep(0.3 * (_attempt + 1))
    else:
        assert last_exc is not None
        raise last_exc

    yield manager


# Factory for the e2e app. Defaults to the OSS app; lemma-cloud overrides this
# (in its conftest, via set_test_app_factory) to compose CLOUD_MODULES so its
# billing e2e suite exercises a billing-aware app.
_test_app_factory = None


def set_test_app_factory(factory) -> None:
    """Override how the e2e ``test_app`` fixture builds its FastAPI app."""
    global _test_app_factory
    _test_app_factory = factory


# One OpenAPI document per distinct route table per test process. Every test
# builds a fresh app, and every `backend_server` runs that app's lifespan, which
# builds the document up front because e2e serves `/openapi.json` -- 1-3s of
# pydantic model-graph work per test, for a document that is identical as long
# as the routes are. Keyed by the route table rather than shared outright, so
# an app factory that mounts different routers still gets its own document.
_OPENAPI_BY_ROUTES: dict[tuple, dict] = {}


def _route_table_key(app) -> tuple:
    # Title and version too: `custom_openapi` writes both into the document.
    return (app.title, app.version) + tuple(
        (
            type(route).__name__,
            getattr(route, "path", ""),
            getattr(route, "name", ""),
            tuple(sorted(getattr(route, "methods", None) or ())),
            bool(getattr(route, "include_in_schema", True)),
        )
        for route in app.routes
    )


def _share_openapi_document(app):
    build = app.openapi

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        key = _route_table_key(app)
        schema = _OPENAPI_BY_ROUTES.get(key)
        if schema is None:
            schema = _OPENAPI_BY_ROUTES[key] = build()
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
    return app


@pytest.fixture(scope="function")
def test_app(e2e_settings, db_manager, monkeypatch, tmp_path):
    _ensure_repo_root_on_path()
    _configure_local_datastore_runtime(monkeypatch, tmp_path)
    _reset_supertokens_testing_state()
    if _test_app_factory is not None:
        return _share_openapi_document(_test_app_factory())
    from app.app import create_app

    return _share_openapi_document(create_app())


@pytest_asyncio.fixture(scope="function")
async def db_session(db_manager) -> AsyncGenerator:
    async with db_manager.session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def e2e_process_clients() -> AsyncGenerator[None, None]:
    """Own the shutdown of the process-wide clients a test may have opened.

    A fixture rather than a `finally` inside `async_client`, because *when*
    this runs is the whole point and only a dependency edge can state it.
    Every fixture that can leave one of these singletons open depends on this
    one, so pytest sets it up first and finalises it last -- after all of them.

    It has to be last because the singletons are not all opened the same way.
    `async_client` opens them lazily, one HTTPX ASGI request at a time, on the
    pytest loop. `backend_server` opens them inside the application's own
    lifespan, running on uvicorn's lifespan task -- and some carry an anyio
    cancel scope bound to whichever task entered it. Closing the streaq queue
    from the wrong task cancels that scope's *host*, which is uvicorn's
    lifespan task parked in `receive()`: the application shutdown then unwinds
    mid-cancellation, stops partway through `app/app.py`'s core closers, and
    prints a cancel-scope `RuntimeError` from the MCP session manager's task
    group on the way out. Green, loud, and directly under whatever else that
    test printed -- which is how it was once read as the cause of an unrelated
    failure.

    The noise was the visible half. The closers that never ran were the other
    one: every test with a `backend_server` leaked the redis connection and
    socket that `close_redis_json_caches` was cancelled in the middle of, and
    reported them as `ResourceWarning`s nobody connected to this.

    Ordered after the server, the application's own lifespan closes what it
    opened, on the task that opened it, and this is left with nothing to do.
    """

    yield
    await _close_e2e_process_clients()


@pytest_asyncio.fixture(scope="function")
async def async_client(
    test_app, e2e_process_clients
) -> AsyncGenerator["AsyncClient", None]:
    from httpx import ASGITransport, AsyncClient

    del e2e_process_clients  # ordering only; see the fixture's docstring
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        yield client


async def _sign_up_verified_user(client: "AsyncClient") -> dict[str, str]:
    """A new, verified, signed-in user, through the real SuperTokens endpoints."""
    email = f"test+module-e2e-{uuid4().hex[:10]}@example.com"
    response = await client.post("/st/auth/signup", json=_credentials(email))
    data = response.json()
    assert response.status_code == 200 and data.get("status") == "OK", data

    await verify_emailpassword_for_tests(data["user"]["id"], email)
    return await _sign_in(client, email)


def _credentials(email: str) -> dict[str, list[dict[str, str]]]:
    return {
        "formFields": [
            {"id": "email", "value": email},
            {"id": "password", "value": _E2E_PASSWORD},
        ]
    }


#: Every e2e user's password. Thrown away with the SuperTokens database.
_E2E_PASSWORD = "TestPassword@123"


async def _sign_in(client: "AsyncClient", email: str) -> dict[str, str]:
    """A new session for an existing user."""
    response = await client.post("/st/auth/signin", json=_credentials(email))
    data = response.json()
    assert response.status_code == 200 and data.get("status") == "OK", data

    access_token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert access_token

    return {"email": email, "token": access_token, "id": data["user"]["id"]}


async def _create_org(client: "AsyncClient", token: str) -> dict[str, object]:
    response = await client.post(
        "/organizations",
        json={"name": f"Module Test Org {uuid4().hex[:8]}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture(scope="function")
async def fixed_test_user(async_client: "AsyncClient"):
    return await _sign_up_verified_user(async_client)


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


@dataclass(frozen=True, slots=True)
class ModuleIdentity:
    """The user and organization a module's tests share."""

    user: dict[str, str]
    org: dict[str, object]


@pytest_asyncio.fixture(scope="module")
async def module_identity(e2e_settings) -> AsyncGenerator[ModuleIdentity, None]:
    """One signed-in user and their organization, shared by a module's tests.

    Signing up through SuperTokens and creating an organization is most of the
    setup of a short e2e test, and most tests only need *a* user and *an*
    organization rather than new ones. This builds them once per module and
    snapshots their rows; ``db_manager`` then resets each test that asks for
    this fixture to exactly those rows, so a test still cannot see anything an
    earlier one wrote -- including changes to the user or organization rows.

    Isolate per test with what the test creates, normally a fresh pod. A test
    that counts users, organizations or members, or that signs the user out
    or changes their credentials, keeps the function-scoped fixtures.
    """
    from httpx import ASGITransport, AsyncClient

    url = e2e_settings.database_url
    manager = DatabaseManager(url)
    _import_e2e_models()
    await _ensure_schema_once(url)
    await _truncate_retrying_deadlocks(manager)
    _reset_supertokens_testing_state()
    app = _test_app_factory() if _test_app_factory is not None else None
    if app is None:
        from app.app import create_app

        app = create_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            user = await _sign_up_verified_user(client)
            org = await _create_org(client, user["token"])
        snapshot = await manager.snapshot(
            _IDENTITY_SNAPSHOT_SCHEMA, exclude=_SNAPSHOT_EXCLUDED_TABLES
        )
        async with manager.engine.connect() as conn:
            internal_user_id = await conn.scalar(
                text("SELECT id::text FROM users WHERE email = :email"),
                {"email": user["email"]},
            )
    finally:
        await _close_e2e_process_clients()
    ids = tuple(
        str(value) for value in (user["id"], internal_user_id, org["id"]) if value
    )
    _IDENTITY_SNAPSHOTS[url] = _IdentitySnapshot(rows=snapshot, ids=tuple(set(ids)))
    try:
        yield ModuleIdentity(user=user, org=org)
    finally:
        _IDENTITY_SNAPSHOTS.pop(url, None)
        await manager.drop_snapshot(snapshot)
        await manager.close()


async def extend_module_identity(
    database_url: str,
    build: Callable[["AsyncClient"], Any],
) -> Any:
    """Add rows to the module's shared identity, once per module.

    For a module whose every test starts from the same world -- an
    organization, a pod and the people in it -- and was building it test by
    test, mostly in sign-ups. ``build`` gets a client signed in as the module's
    user on a fresh app, starting from the identity's rows alone, and returns
    ``(value, ids)``: what the tests need, and the ids of what it made, so their
    cached Redis keys are dropped before each test the way the identity's are.
    Its rows then join the snapshot ``db_manager`` restores, so every later
    test of the module starts from them, and from nothing a test wrote.

    Call it from a module-scoped fixture that depends on ``module_identity``.
    """
    from httpx import ASGITransport, AsyncClient

    identity = _IDENTITY_SNAPSHOTS.get(database_url)
    if identity is None:
        raise RuntimeError("extend_module_identity needs module_identity first")
    manager = _shared_db_manager(database_url)
    await _truncate_retrying_deadlocks(manager, restore=identity.rows)
    _reset_supertokens_testing_state()
    app = _test_app_factory() if _test_app_factory is not None else None
    if app is None:
        from app.app import create_app

        app = create_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            value, ids = await build(client)
        rows = await manager.snapshot(
            _IDENTITY_SNAPSHOT_SCHEMA, exclude=_SNAPSHOT_EXCLUDED_TABLES
        )
        # Every user, organization and pod now in the snapshot is one a test
        # may cache something under.
        async with manager.engine.connect() as conn:
            for table in ("users", "organizations", "pods"):
                ids = [
                    *ids,
                    *(await conn.scalars(text(f"SELECT id::text FROM {table}"))),
                ]
    finally:
        await _close_e2e_process_clients()
    _IDENTITY_SNAPSHOTS[database_url] = _IdentitySnapshot(
        rows=rows, ids=tuple({*identity.ids, *(str(i) for i in ids if i)})
    )
    return value


@pytest_asyncio.fixture(scope="function")
async def module_test_user(module_identity, db_manager) -> dict[str, str]:
    """The module's shared user, in place of ``fixed_test_user``.

    Depends on ``db_manager`` so the reset that restores the user's rows has
    run before anything uses them.
    """
    del db_manager  # ordering only
    return module_identity.user


@pytest_asyncio.fixture(scope="function")
async def module_test_user_own_session(
    module_identity, db_manager, async_client: "AsyncClient"
) -> dict[str, str]:
    """The module's shared user, signed in afresh for this test alone.

    For a module whose tests revoke, expire or inspect the session itself: a
    sign-in is a fraction of a sign-up, and what one test does to its session
    cannot reach the next.
    """
    del db_manager  # ordering only
    return await _sign_in(async_client, module_identity.user["email"])


@pytest_asyncio.fixture(scope="function")
async def module_test_org(module_identity, db_manager) -> dict[str, object]:
    """The module's shared organization, in place of ``fixed_test_org``."""
    del db_manager  # ordering only
    return module_identity.org


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
