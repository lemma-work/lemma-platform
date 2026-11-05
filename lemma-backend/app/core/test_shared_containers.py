"""Containers shared across pytest-xdist workers, and kept between sessions.

Split out of ``test_utils``: these fixtures coordinate one Postgres, Redis and
Kreuzberg per test run (or per machine, with ``LEMMA_E2E_REUSE=1``) on top of
the single-container helpers that module defines.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import contextmanager
from typing import Generator

import psycopg

from app.core.test_utils import (
    KREUZBERG_IMAGE,
    POSTGRES_DB,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    REDIS_IMAGE,
    REUSE_LABEL,
    SHARED_E2E_NETWORK_NAME,
    LemmaDockerContainer,
    LemmaPostgresContainer,
    _ensure_docker_network,
    _env_int,
    _wait_for_kreuzberg_ready,
    _wait_for_postgres,
    _wait_for_tcp,
    container_running,
    get_kreuzberg_container,
    get_kreuzberg_url,
    get_postgres_container,
    get_redis_container,
    postgres_server_command,
    remove_named_container,
)


def _session_scoped_container_name(base_name: str, basetemp_parent) -> str:
    """Make a shared-container name unique to THIS pytest invocation.

    Under xdist, ``tmp_path_factory.getbasetemp()`` for a worker is a
    ``popen-gwN`` subdirectory of a per-invocation, monotonically-numbered
    ``pytest-<N>`` root -- so ``.parent`` (what callers pass in as
    ``basetemp_parent``) is stable across all workers of ONE invocation but
    different for every separate invocation (confirmed empirically: two
    `pytest` runs in a row get `pytest-355` and `pytest-356`; xdist workers
    of one run share the same parent). A bare fixed name here would instead
    be the same across completely unrelated invocations -- e.g. two
    developers running the suite at once, or two worktrees sharing a Docker
    daemon -- and the second invocation's ``docker rm -f <name>`` would rip
    out the first invocation's still-live container out from under it,
    independent of (and invisible to) the first invocation's own refcount.
    Observed exactly this: a concurrent worktree's run corrupted another's
    containers mid-test with connection-refused errors.
    """
    digest = hashlib.sha256(str(basetemp_parent).encode()).hexdigest()[:10]
    return f"{base_name}-{digest}"


def start_shared_kreuzberg(name: str) -> str:
    """Start ONE named Kreuzberg container and return its URL.

    Kreuzberg bundles an embedding model and is RAM-heavy; one container per
    xdist worker OOMs most machines. The e2e suite runs a single shared instance
    across all workers (coordinated by a file lock in the datastore conftest) —
    this starts it under a fixed name so it's discoverable and removable from any
    worker. Not auto-stopped; the last worker out calls ``remove_named_container``
    (and the label-based prune sweeps any straggler).
    """
    # Clear any straggler with this name from a previously crashed run.
    subprocess.run(["docker", "rm", "-f", "-v", name], check=False, capture_output=True)
    container = LemmaDockerContainer(KREUZBERG_IMAGE, 8000).with_run_args(
        "--name", name, "--restart", "unless-stopped"
    )
    container.__enter__()  # start detached; intentionally no matching __exit__
    _wait_for_kreuzberg_ready(container)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(8000)
    return f"http://{host}:{port}"


SHARED_KREUZBERG_NAME = "lemma-e2e-kreuzberg-shared"


@contextmanager
def shared_kreuzberg(basetemp_parent, worker_id: str) -> Generator[str, None, None]:
    """Yield the URL of a SINGLE Kreuzberg shared across all xdist workers.

    Kreuzberg bundles an embedding model and is RAM-heavy; one container per
    worker OOMs most machines. Refcounted via a file lock in the xdist-shared
    temp root: the first user starts one named container and records its URL;
    others reuse it; the last user out removes it. Used by BOTH the datastore
    kreuzberg fixture and the streaq worker fixture (the worker indexes datastore
    files, so it must point at the same container). Without xdist
    (``worker_id == 'master'``) it falls back to a plain per-session container.
    """
    if worker_id == "master":
        with get_kreuzberg_container() as kb:
            yield get_kreuzberg_url(kb)
        return

    from pathlib import Path

    from filelock import FileLock

    root = Path(basetemp_parent)
    name = _session_scoped_container_name(SHARED_KREUZBERG_NAME, basetemp_parent)
    lock = FileLock(str(root / "kreuzberg.lock"))
    url_file = root / "kreuzberg_url.txt"
    refs_file = root / "kreuzberg_refs.txt"

    with lock:
        if url_file.exists():
            url = url_file.read_text().strip()
        else:
            url = start_shared_kreuzberg(name)
            url_file.write_text(url)
        refs = int(refs_file.read_text()) if refs_file.exists() else 0
        refs_file.write_text(str(refs + 1))

    try:
        yield url
    finally:
        with lock:
            refs = (int(refs_file.read_text()) if refs_file.exists() else 1) - 1
            refs_file.write_text(str(refs))
            if refs <= 0:
                remove_named_container(name)
                url_file.unlink(missing_ok=True)
                refs_file.unlink(missing_ok=True)


#: Opt-in, local only: keep Postgres, Redis and SuperTokens running between
#: pytest sessions instead of starting them for every run. Each session still
#: gets fresh databases (cloned from a schema template), so tests see exactly
#: what a cold run gives them. CI never sets it.
E2E_REUSE_ENV = "LEMMA_E2E_REUSE"


def e2e_reuse_requested() -> bool:
    return os.environ.get(E2E_REUSE_ENV, "").strip().lower() in ("1", "true", "yes")


def reused_container_name(role: str) -> str:
    """A stable name per checkout, so two worktrees never share containers."""
    checkout = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    digest = hashlib.sha256(checkout.encode()).hexdigest()[:8]
    return f"lemma-e2e-reuse-{digest}-{role}"


def reused_postgres() -> LemmaPostgresContainer:
    """The checkout's long-lived Postgres, started if it is not already running."""
    name = reused_container_name("postgres")
    if not container_running(name) or _started_with(name) != postgres_server_command():
        # A container from before the server settings changed would keep the
        # old ones for as long as it lived, which for a reused one is forever.
        # The checkout's reused SuperTokens cores built their tables in the
        # old server's databases, so they go with it.
        _remove_reused(reused_container_name("supertokens-"))
        start_shared_postgres(name, reuse=True)
    container = LemmaPostgresContainer()
    container.container_id = name
    _wait_for_postgres(container)
    return container


def _started_with(name: str) -> list[str] | None:
    """The command a container was started with, or None if Docker cannot say."""
    import json

    result = subprocess.run(
        ["docker", "inspect", "-f", "{{json .Config.Cmd}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def reused_redis() -> LemmaDockerContainer:
    name = reused_container_name("redis")
    if not container_running(name):
        start_shared_redis(name, reuse=True)
    container = LemmaDockerContainer(REDIS_IMAGE, 6379)
    container.container_id = name
    _wait_for_tcp(container, 6379, _env_int("REDIS_STARTUP_TIMEOUT_SECONDS", 120))
    return container


def remove_reused_containers() -> list[str]:
    """Stop and remove every reused container of this checkout (``make e2e-reuse-down``)."""
    return _remove_reused(reused_container_name(""))


def _remove_reused(name_prefix: str) -> list[str]:
    """Remove this checkout's reused containers whose name starts with ``name_prefix``."""
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label={REUSE_LABEL}",
            "--filter",
            f"name={name_prefix}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ids = result.stdout.split()
    for container_id in ids:
        remove_named_container(container_id)
    return ids


def recreate_postgres_database(
    container: LemmaPostgresContainer,
    database_name: str,
    *,
    template: str | None = None,
) -> None:
    """Give ``database_name`` a fresh database (cloned from ``template``).

    The old one is renamed aside rather than dropped: DROP DATABASE forces a
    checkpoint, which is most of a reused session's setup time on a laptop.
    ``drop_set_aside_databases`` removes them off the critical path.
    """
    with psycopg.connect(_admin_dsn(container), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
        ).fetchone()
        if exists is not None:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            aside = f"{_ASIDE_PREFIX}{hashlib.sha256(os.urandom(8)).hexdigest()[:12]}"
            conn.execute(f'ALTER DATABASE "{database_name}" RENAME TO "{aside}"')
        clause = f' TEMPLATE "{template}"' if template else ""
        conn.execute(f'CREATE DATABASE "{database_name}"{clause}')


_ASIDE_PREFIX = "lemma_e2e_aside_"


def drop_set_aside_databases(container: LemmaPostgresContainer) -> None:
    """Drop every database ``recreate_postgres_database`` moved aside (best effort)."""
    with psycopg.connect(_admin_dsn(container), autocommit=True) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT datname FROM pg_database WHERE starts_with(datname, %s)",
                (_ASIDE_PREFIX,),
            ).fetchall()
        ]
        for name in names:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _admin_dsn(container: LemmaPostgresContainer) -> str:
    return (
        f"host={container.get_container_host_ip()} "
        f"port={container.get_exposed_port(5432)} "
        f"user={container.username} "
        f"password={container.password} "
        f"dbname={container.dbname}"
    )


def start_shared_postgres(name: str, *, reuse: bool = False) -> LemmaPostgresContainer:
    """Start ONE named Postgres container and return a reference to it.

    Mirrors ``start_shared_kreuzberg``: one Postgres *server process* shared
    across all xdist workers (each worker still gets its own logical
    database inside it via ``create_postgres_database``), instead of one
    full container per worker. Not auto-stopped; the last worker out calls
    ``remove_named_container`` (and the label-based prune sweeps any
    straggler).
    """
    # Clear any straggler with this name from a previously crashed run.
    subprocess.run(["docker", "rm", "-f", "-v", name], check=False, capture_output=True)
    _ensure_docker_network(SHARED_E2E_NETWORK_NAME)
    container = (
        LemmaPostgresContainer()
        .with_env("POSTGRES_USER", POSTGRES_USER)
        .with_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        .with_env("POSTGRES_DB", POSTGRES_DB)
        .with_run_args("--name", name, "--network", SHARED_E2E_NETWORK_NAME)
    )
    if reuse:
        container.label = REUSE_LABEL
    container.__enter__()  # start detached; intentionally no matching __exit__
    container.container_id = name
    _wait_for_postgres(container)
    return container


SHARED_POSTGRES_NAME = "lemma-e2e-postgres-shared"


@contextmanager
def shared_postgres(
    basetemp_parent, worker_id: str
) -> Generator[LemmaPostgresContainer, None, None]:
    """Yield a ``LemmaPostgresContainer`` pointed at a SINGLE Postgres server
    shared across all xdist workers.

    Refcounted via a file lock in the xdist-shared temp root, same pattern as
    ``shared_kreuzberg``: the first worker in starts a named container; later
    workers reconstruct a lightweight reference to it (docker accepts a
    container NAME anywhere it accepts an ID, so ``get_exposed_port``/
    ``get_logs`` -- which just shell out to ``docker port``/``docker logs`` --
    work identically); the last worker out removes it. Without xdist
    (``worker_id == 'master'``) it falls back to a plain per-session
    container.
    """
    if e2e_reuse_requested():
        # The checkout's long-lived container (LEMMA_E2E_REUSE); never removed here.
        yield reused_postgres()
        return
    if worker_id == "master":
        with get_postgres_container() as postgres:
            yield postgres
        return

    from pathlib import Path

    from filelock import FileLock

    root = Path(basetemp_parent)
    name = _session_scoped_container_name(SHARED_POSTGRES_NAME, basetemp_parent)
    lock = FileLock(str(root / "postgres.lock"))
    refs_file = root / "postgres_refs.txt"

    with lock:
        refs = int(refs_file.read_text()) if refs_file.exists() else 0
        if refs == 0:
            start_shared_postgres(name)
        refs_file.write_text(str(refs + 1))

    container = LemmaPostgresContainer()
    container.container_id = name

    try:
        yield container
    finally:
        with lock:
            refs = (int(refs_file.read_text()) if refs_file.exists() else 1) - 1
            refs_file.write_text(str(refs))
            if refs <= 0:
                remove_named_container(name)
                refs_file.unlink(missing_ok=True)
                # SHARED_E2E_NETWORK_NAME is NOT removed here: it is one fixed,
                # permanent resource reused by every invocation on this
                # machine, not owned by this one -- see its own comment.


def start_shared_redis(name: str, *, reuse: bool = False) -> LemmaDockerContainer:
    """Start ONE named Redis container and return a reference to it.

    Mirrors ``start_shared_postgres``: one Redis *server process* shared
    across all xdist workers (each worker gets its own logical database
    inside it, selected via the connection URL's ``/<n>`` suffix -- Redis has
    a native per-connection ``SELECT <db-index>`` concept, so unlike Postgres
    there is no ``CREATE DATABASE``-equivalent step needed), instead of one
    full container per worker. Not auto-stopped; the last worker out calls
    ``remove_named_container`` (and the label-based prune sweeps any
    straggler).
    """
    # Clear any straggler with this name from a previously crashed run.
    subprocess.run(["docker", "rm", "-f", "-v", name], check=False, capture_output=True)
    container = LemmaDockerContainer(REDIS_IMAGE, 6379).with_run_args("--name", name)
    if reuse:
        container.label = REUSE_LABEL
    container.__enter__()  # start detached; intentionally no matching __exit__
    _wait_for_tcp(container, 6379, _env_int("REDIS_STARTUP_TIMEOUT_SECONDS", 120))
    return container


SHARED_REDIS_NAME = "lemma-e2e-redis-shared"


@contextmanager
def shared_redis(
    basetemp_parent, worker_id: str
) -> Generator[LemmaDockerContainer, None, None]:
    """Yield a ``LemmaDockerContainer`` pointed at a SINGLE Redis server
    shared across all xdist workers.

    Refcounted via a file lock in the xdist-shared temp root, same pattern as
    ``shared_postgres``/``shared_kreuzberg``: the first worker in starts a
    named container; later workers reconstruct a lightweight reference to it
    (docker accepts a container NAME anywhere it accepts an ID, so
    ``get_exposed_port``/``get_logs`` -- which just shell out to ``docker
    port``/``docker logs`` -- work identically); the last worker out removes
    it. Without xdist (``worker_id == 'master'``) it falls back to a plain
    per-session container.
    """
    if e2e_reuse_requested():
        yield reused_redis()
        return
    if worker_id == "master":
        with get_redis_container() as redis:
            yield redis
        return

    from pathlib import Path

    from filelock import FileLock

    root = Path(basetemp_parent)
    name = _session_scoped_container_name(SHARED_REDIS_NAME, basetemp_parent)
    lock = FileLock(str(root / "redis.lock"))
    refs_file = root / "redis_refs.txt"

    with lock:
        refs = int(refs_file.read_text()) if refs_file.exists() else 0
        if refs == 0:
            start_shared_redis(name)
        refs_file.write_text(str(refs + 1))

    container = LemmaDockerContainer(REDIS_IMAGE, 6379)
    container.container_id = name

    try:
        yield container
    finally:
        with lock:
            refs = (int(refs_file.read_text()) if refs_file.exists() else 1) - 1
            refs_file.write_text(str(refs))
            if refs <= 0:
                remove_named_container(name)
                refs_file.unlink(missing_ok=True)
