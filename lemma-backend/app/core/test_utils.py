import hashlib
import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional

import psycopg

# Use same images as docker-compose.yml for consistency. pgvector 0.8.3 is
# required for the halfvec vector indexes the search service now builds.
POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg18"
REDIS_IMAGE = "redis:7.4-alpine"
SUPERTOKENS_IMAGE = "docker.io/supertokens/supertokens-postgresql:11.4.5"
# Kreuzberg 4.8.0-4.9.9 shipped under the Elastic License 2.0, which forbids
# offering the software as a managed service; 4.10.0 relicensed back to MIT.
# ghcr.io/xberg-io/kreuzberg:4.9.9-core labels itself MIT, but the label is wrong
# — the revision it names as its source (54dcb33a) carries an ELv2 LICENSE — so
# that tag must not be used. 4.10.2 is the MIT LTS head and speaks the same v4
# wire schema as 4.9.9, so no client change is needed.
# The -core image fetches layout/OCR models from HuggingFace on first use.
KREUZBERG_IMAGE = "ghcr.io/kreuzberg-dev/kreuzberg-core:4.10.2"
POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"
DOCKER_LABEL = "lemma.e2e=true"


class LemmaDockerContainer:
    def __init__(self, image: str, internal_port: int) -> None:
        self.image = image
        self.internal_port = internal_port
        self.container_id: str | None = None
        self._env: dict[str, str] = {}
        self._extra_run_args: list[str] = []
        self._command: list[str] = []
        #: The session sweep deletes everything labelled DOCKER_LABEL; a
        #: container meant to outlive the session carries REUSE_LABEL instead.
        self.label = DOCKER_LABEL

    def with_env(self, name: str, value: str) -> "LemmaDockerContainer":
        self._env[name] = value
        return self

    def with_run_args(self, *args: str) -> "LemmaDockerContainer":
        """Append extra ``docker run`` flags (e.g. ``--memory``, ``--restart``)."""
        self._extra_run_args.extend(args)
        return self

    def with_command(self, *args: str) -> "LemmaDockerContainer":
        """Set the container's command, appended after the image name."""
        self._command.extend(args)
        return self

    def __enter__(self) -> "LemmaDockerContainer":
        command = [
            "docker",
            "run",
            "-d",
            "--label",
            self.label,
            "-p",
            f"127.0.0.1::{self.internal_port}",
        ]
        for name, value in self._env.items():
            command.extend(["-e", f"{name}={value}"])
        command.extend(self._extra_run_args)
        command.append(self.image)
        # After the image, so this is the container's command rather than a
        # `docker run` flag. Images with a usable ENTRYPOINT supply nothing
        # here.
        command.extend(self._command)

        result = subprocess.run(command, check=True, capture_output=True, text=True)
        self.container_id = result.stdout.strip()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.container_id:
            # -v also removes the container's anonymous data volume (e.g.
            # postgres/pgvector, supertokens, and kreuzberg all declare VOLUME
            # in their image) — without it, every teardown, even a clean one,
            # leaked one volume forever.
            subprocess.run(
                ["docker", "rm", "-f", "-v", self.container_id],
                check=False,
                capture_output=True,
            )
            self.container_id = None

    def get_container_host_ip(self) -> str:
        return "127.0.0.1"

    def get_exposed_port(self, port: int) -> str:
        if not self.container_id:
            raise RuntimeError("Container has not been started")
        result = subprocess.run(
            ["docker", "port", self.container_id, f"{port}/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        endpoint = result.stdout.strip().splitlines()[0]
        return endpoint.rsplit(":", 1)[1]

    def get_logs(self) -> bytes:
        if not self.container_id:
            return b""
        result = subprocess.run(
            ["docker", "logs", self.container_id],
            check=False,
            capture_output=True,
        )
        return result.stdout + result.stderr


#: Server settings for a throwaway test database. Every test Postgres is
#: discarded at the end of its session, so durability buys nothing: an fsync per
#: commit is pure latency on a suite that commits thousands of tiny
#: transactions, and a crash just means a rerun. ``max_connections`` is sized
#: for xdist: each worker opens the app's pools, a worker subprocess's pools and
#: SuperTokens' own, and the image's default of 100 was reached at ``-n 3``.
#: ``jit`` only ever costs time on queries this small.
TEST_POSTGRES_SERVER_SETTINGS: tuple[str, ...] = (
    "max_connections=300",
    "fsync=off",
    "synchronous_commit=off",
    "full_page_writes=off",
    "shared_buffers=256MB",
    "max_wal_size=4GB",
    "checkpoint_timeout=30min",
    "jit=off",
)


def postgres_server_command() -> list[str]:
    """The ``postgres`` command line every test container is started with."""
    command = ["postgres"]
    for setting in TEST_POSTGRES_SERVER_SETTINGS:
        command += ["-c", setting]
    return command


class LemmaPostgresContainer(LemmaDockerContainer):
    username = POSTGRES_USER
    password = POSTGRES_PASSWORD
    dbname = POSTGRES_DB

    def __init__(self) -> None:
        super().__init__(POSTGRES_IMAGE, 5432)
        # Only read by ``__enter__``; a reference built around a running
        # container's name never starts one, so carrying it there is inert.
        # Docker's 64 MB /dev/shm is below the shared memory 300 connections
        # and the bigger buffer pool ask for.
        self.with_run_args("--shm-size=512m")
        self.with_command(*postgres_server_command())


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# (docker filter, hours a STOPPED matching container may sit before reaping).
# Thresholds are generous per resource kind, not a single global number:
# lemma.e2e testcontainers finish in minutes, so 2h is already a wide margin;
# managed-by=lemma-workspace is also the PRODUCTION sandbox label, and a dev
# may run a real sandbox locally for an afternoon, so it gets 12h; a
# load-test session (build the fixture stack, run k6, inspect the result) can
# reasonably span a few hours, so lemma-load-* gets 6h.
_REAPABLE_CONTAINER_FILTERS: tuple[tuple[str, float], ...] = (
    (f"label={DOCKER_LABEL}", 2.0),
    ("label=managed-by=lemma-workspace", 12.0),
    ("name=^lemma-load-", 6.0),
)
# Only the workspace provider's own named sandbox volumes (lemma-vol-*) are
# swept here — anonymous container volumes are handled by passing -v to every
# `docker rm -f` above, and `docker volume prune` (see `make docker-reap`)
# already only ever touches volumes with zero references.
_REAPABLE_VOLUME_FILTER: tuple[str, float] = ("label=managed-by=lemma-workspace", 12.0)


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _parse_docker_timestamp(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stopped_container_age_hours(container_id: str) -> Optional[float]:
    """Hours since creation, or None if the container is running/unknown.

    A running container is never a reap candidate regardless of age — that is
    what makes this safe to call unconditionally on a shared dev machine: a
    concurrently active session's containers are always excluded, full stop.
    """
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.Created}}",
            container_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or "|" not in result.stdout:
        return None
    running, created = result.stdout.strip().split("|", 1)
    if running == "true":
        return None
    created_at = _parse_docker_timestamp(created)
    if created_at is None:
        return None
    return (datetime.now(timezone.utc) - created_at).total_seconds() / 3600


def reap_stale_lemma_containers() -> list[str]:
    """Force-remove STOPPED Lemma-managed containers past their grace period.

    Best-effort cleanup for containers a crashed/hard-killed test or sandbox
    process never got to tear down in its own `finally`/fixture teardown.
    Age-gated per `_REAPABLE_CONTAINER_FILTERS`, so it is safe to run
    unconditionally: nothing still running is ever touched, so it can't
    disturb a concurrently running e2e/sandbox/load-test session on the same
    Docker daemon.
    """
    if not _has_docker():
        return []
    removed: list[str] = []
    seen: set[str] = set()
    for docker_filter, max_age_hours in _REAPABLE_CONTAINER_FILTERS:
        list_result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", docker_filter],
            capture_output=True,
            text=True,
            check=False,
        )
        if list_result.returncode != 0:
            continue
        for container_id in list_result.stdout.splitlines():
            container_id = container_id.strip()
            if not container_id or container_id in seen:
                continue
            seen.add(container_id)
            age_hours = _stopped_container_age_hours(container_id)
            if age_hours is None or age_hours < max_age_hours:
                continue
            subprocess.run(
                ["docker", "rm", "-f", "-v", container_id],
                check=False,
                capture_output=True,
            )
            removed.append(container_id)
    return removed


def reap_stale_lemma_volumes() -> list[str]:
    """Force-remove named ``lemma-vol-*`` sandbox volumes past their grace period.

    ``docker volume rm`` refuses (harmlessly — ``check=False``) a volume still
    attached to any container, so this can never touch a volume backing a live
    sandbox even if that sandbox happens to be older than the threshold.
    """
    if not _has_docker():
        return []
    docker_filter, max_age_hours = _REAPABLE_VOLUME_FILTER
    list_result = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", docker_filter],
        capture_output=True,
        text=True,
        check=False,
    )
    if list_result.returncode != 0:
        return []
    removed: list[str] = []
    for name in list_result.stdout.splitlines():
        name = name.strip()
        if not name:
            continue
        inspect_result = subprocess.run(
            ["docker", "volume", "inspect", "--format", "{{.CreatedAt}}", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspect_result.returncode != 0:
            continue
        created_at = _parse_docker_timestamp(inspect_result.stdout)
        if created_at is None:
            continue
        age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
        if age_hours < max_age_hours:
            continue
        rm_result = subprocess.run(
            ["docker", "volume", "rm", name], check=False, capture_output=True
        )
        if rm_result.returncode == 0:
            removed.append(name)
    return removed


def reap_stale_lemma_resources() -> None:
    """Best-effort cleanup for stale E2E/sandbox/load-test Docker resources."""
    reap_stale_lemma_containers()
    reap_stale_lemma_volumes()


#: How often a starting container is probed. Each local e2e run waits on
#: Postgres, then SuperTokens, then Redis before its first test; a coarse poll
#: is paid on top of the real start time, every run.
_READINESS_POLL_SECONDS = 0.1


def _ensure_docker_network(name: str) -> None:
    """Create a user-defined bridge network if it doesn't already exist.

    Container-to-container DNS-by-name only works on a user-defined network --
    the default ``bridge`` network an unqualified ``docker run`` joins does
    not provide it, and routing back out through a host-published port
    doesn't work either (see ``get_postgres_uri_from_another_container``).
    Idempotent and racy on purpose: ``docker network create`` on a name that
    already exists just errors, which this swallows, since whichever
    container starts first on a given machine creates it for every other one
    after it.
    """
    subprocess.run(
        ["docker", "network", "create", name], check=False, capture_output=True
    )


def container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def remove_named_container(name: str) -> None:
    """Force-remove a container by name, and its anonymous volumes (best effort)."""
    subprocess.run(["docker", "rm", "-f", "-v", name], check=False, capture_output=True)


REUSE_LABEL = "lemma.e2e.reuse=true"


# ONE fixed name, not digested per invocation like the container names above.
# A user-defined network consumes a subnet from Docker's finite default
# address pool on creation (historically ~31 of them); a network per pytest
# invocation is exactly the shape that exhausted it before -- see the
# "prune orphaned lemma-e2e-* networks" comment in e2e_base.py's
# _cleanup_e2e_workspace_containers, from before the shared-Postgres model,
# when every testcontainer got its own network. Reusing one well-known name
# forever means at most one network ever exists, and there is nothing to
# accumulate: many unrelated invocations' differently (and uniquely) named
# containers can share it with no cross-talk risk, since reaching another
# container by name requires already knowing its invocation-specific name.
SHARED_E2E_NETWORK_NAME = "lemma-e2e-shared-net"


def _wait_for_tcp(
    container: LemmaDockerContainer, port: int, timeout_seconds: int
) -> None:
    deadline = time.monotonic() + timeout_seconds
    host = container.get_container_host_ip()
    exposed_port = int(container.get_exposed_port(port))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, exposed_port), timeout=2):
                return
        except OSError:
            time.sleep(_READINESS_POLL_SECONDS)
    raise RuntimeError(f"{container.image} did not open port {port} in time")


def _wait_for_postgres(container: LemmaPostgresContainer) -> None:
    deadline = time.monotonic() + _env_int("POSTGRES_STARTUP_TIMEOUT_SECONDS", 120)
    dsn = (
        f"host={container.get_container_host_ip()} "
        f"port={container.get_exposed_port(5432)} "
        f"user={container.username} "
        f"password={container.password} "
        f"dbname={container.dbname}"
    )
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, autocommit=True):
                return
        except psycopg.OperationalError:
            time.sleep(_READINESS_POLL_SECONDS)
    logs = container.get_logs().decode("utf-8", errors="replace")
    raise RuntimeError(f"Postgres did not become ready in time.\n{logs}")


@contextmanager
def get_postgres_container() -> Generator[LemmaPostgresContainer, None, None]:
    """
    Starts a PostgreSQL container and yields it.
    Can be used in pytest fixtures with scope="session".

    Only used serially (no xdist, e.g. ``make test-connection-scope``'s
    ``-m connection_scope`` has no ``-n``) -- ``shared_postgres``'s xdist path
    goes through ``start_shared_postgres`` instead. Named and networked the
    same way regardless: ``get_postgres_uri_from_another_container`` needs
    ``container.container_id`` to be a real Docker name, not the bare hex id
    an unqualified ``docker run`` would otherwise leave it as, and SuperTokens
    needs ``SHARED_E2E_NETWORK_NAME`` to already exist to join it.
    """
    name = f"lemma-e2e-postgres-{hashlib.sha256(f'{os.getpid()}-{time.time()}'.encode()).hexdigest()[:10]}"
    _ensure_docker_network(SHARED_E2E_NETWORK_NAME)
    container = (
        LemmaPostgresContainer()
        .with_env("POSTGRES_USER", POSTGRES_USER)
        .with_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        .with_env("POSTGRES_DB", POSTGRES_DB)
        .with_run_args("--name", name, "--network", SHARED_E2E_NETWORK_NAME)
    )

    with container as postgres:
        # __enter__ just overwrote container_id with `docker run`'s stdout --
        # the container's full hex id, not the --name given above. Docker's
        # embedded DNS resolves the name, not that id, so this must be reset
        # before anything (SuperTokens, via get_postgres_uri_from_another_
        # container) tries to reach this container by it -- the same reset
        # shared_postgres's xdist path already does for the same reason.
        postgres.container_id = name
        _wait_for_postgres(postgres)
        yield postgres


@contextmanager
def get_redis_container() -> Generator[LemmaDockerContainer, None, None]:
    """
    Starts a Redis container and yields it.
    Can be used in pytest fixtures with scope="session".
    """
    container = LemmaDockerContainer(REDIS_IMAGE, 6379)
    with container as redis:
        _wait_for_tcp(redis, 6379, _env_int("REDIS_STARTUP_TIMEOUT_SECONDS", 120))
        yield redis


@contextmanager
def get_supertokens_container(
    postgres_connection_uri: Optional[str] = None,
    network: Optional[str] = None,
    reuse_name: Optional[str] = None,
) -> Generator[LemmaDockerContainer, None, None]:
    """
    Starts a SuperTokens container.

    ``postgres_connection_uri``, if given, points the core at a real Postgres
    database instead of its no-config in-memory SQLite fallback. SQLite's
    shared-cache mode only supports one writer at a time -- concurrent
    signups (e.g. asyncio.gather()-ing several actors' signup calls in
    create_role_visibility_context) hit real
    ``SQLITE_LOCKED_SHAREDCACHE: database table is locked`` errors under it.
    The image is literally named "supertokens-postgresql"; SQLite is a
    quick-start fallback, not the intended concurrent-write-safe mode.

    ``network``, if given, joins this container to the same user-defined
    Docker network as ``postgres_connection_uri``'s target (see
    ``get_postgres_uri_from_another_container``) -- required for the
    container-name hostname in that URI to resolve at all.
    """
    container = _supertokens_container(postgres_connection_uri, network)
    if reuse_name is not None:
        if not container_running(reuse_name):
            remove_named_container(reuse_name)
            container.label = REUSE_LABEL
            container.with_run_args("--name", reuse_name)
            container.__enter__()
        container.container_id = reuse_name
        _wait_for_supertokens(container)
        yield container
        return

    with container as st:
        _wait_for_supertokens(st)
        yield st


def _supertokens_container(
    postgres_connection_uri: Optional[str], network: Optional[str]
) -> LemmaDockerContainer:
    # Every user signup/signin in the suite pays real bcrypt cost against the
    # default work factor (2^11 rounds). This container is thrown away at the
    # end of the run and never holds anything worth protecting, so drop the
    # rounds to the minimum SuperTokens accepts -- tests that create several
    # actors (e.g. visibility-matrix fixtures signing up 3+ users) see that
    # multiplied per signup.
    container = LemmaDockerContainer(SUPERTOKENS_IMAGE, 3567).with_env(
        "BCRYPT_LOG_ROUNDS",
        os.getenv("E2E_SUPERTOKENS_BCRYPT_LOG_ROUNDS", "4"),
    )
    if postgres_connection_uri:
        container = container.with_env(
            "POSTGRESQL_CONNECTION_URI", postgres_connection_uri
        )
    if network:
        container = container.with_run_args("--network", network)
    return container


def _wait_for_supertokens(st: LemmaDockerContainer) -> None:
    """Poll SuperTokens' health endpoint until it answers."""
    import time
    import urllib.request
    import urllib.error

    host = st.get_container_host_ip()
    port = st.get_exposed_port(3567)
    health_url = f"http://{host}:{port}/hello"

    startup_timeout_seconds = _env_int("SUPERTOKENS_STARTUP_TIMEOUT_SECONDS", 120)
    # Fine-grained: every local e2e run waits on this before its first
    # test, and a whole-second poll spent up to a second past readiness.
    poll_interval_seconds = max(
        0.05, float(os.getenv("SUPERTOKENS_STARTUP_POLL_SECONDS", "0.2"))
    )
    max_retries = max(1, int(startup_timeout_seconds / poll_interval_seconds))
    for i in range(max_retries):
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    break
        except urllib.error.HTTPError as exc:
            # ``HTTPError`` is also a file-like response. Close it even
            # though ``urlopen`` raised before the context manager entered.
            exc.close()
        except (
            urllib.error.URLError,
            ConnectionRefusedError,
            TimeoutError,
            ConnectionResetError,
        ):
            pass
        time.sleep(poll_interval_seconds)
    else:
        logs = st.get_logs()
        if isinstance(logs, tuple):
            stdout = logs[0].decode("utf-8", errors="replace")
            stderr = logs[1].decode("utf-8", errors="replace")
            log_output = f"stdout:\n{stdout}\nstderr:\n{stderr}"
        else:
            log_output = logs.decode("utf-8", errors="replace")
        raise RuntimeError(
            "SuperTokens did not become ready "
            f"after {startup_timeout_seconds} seconds.\n{log_output}"
        )


@contextmanager
def get_kreuzberg_container() -> Generator[LemmaDockerContainer, None, None]:
    """Starts a Kreuzberg container and waits for /health.

    Kreuzberg's extraction process can grow memory across requests and OOM-crash
    under the cumulative load of a full e2e module run; the session-scoped
    container would then refuse every later extraction. Bound its memory and let
    Docker restart it on failure — ``KreuzbergHelper`` retries transient
    connection errors with backoff, so a restart is transparent to callers.
    """
    # No memory cap: a low cap makes the OOM killer fire *sooner*. Instead let
    # Docker restart the container if its extraction process crashes; the
    # KreuzbergHelper retry budget (below) is wide enough to ride out a restart.
    container = LemmaDockerContainer(KREUZBERG_IMAGE, 8000).with_run_args(
        "--restart", "unless-stopped"
    )

    with container as kb:
        _wait_for_kreuzberg_ready(kb)
        yield kb


def _wait_for_kreuzberg_ready(container: LemmaDockerContainer) -> None:
    """Poll Kreuzberg's /health until ready, else raise with the container logs."""
    import time
    import urllib.request
    import urllib.error

    host = container.get_container_host_ip()
    port = container.get_exposed_port(8000)
    health_url = f"http://{host}:{port}/health"

    startup_timeout_seconds = _env_int("KREUZBERG_STARTUP_TIMEOUT_SECONDS", 120)
    poll_interval_seconds = _env_int("KREUZBERG_STARTUP_POLL_SECONDS", 2)
    max_retries = max(1, startup_timeout_seconds // max(1, poll_interval_seconds))
    for _ in range(max_retries):
        try:
            with urllib.request.urlopen(health_url, timeout=5) as response:
                if response.status == 200:
                    return
        except urllib.error.HTTPError as exc:
            exc.close()
        except (
            urllib.error.URLError,
            ConnectionRefusedError,
            TimeoutError,
            ConnectionResetError,
        ):
            pass
        time.sleep(poll_interval_seconds)

    logs = container.get_logs()
    if isinstance(logs, tuple):
        stdout = logs[0].decode("utf-8", errors="replace")
        stderr = logs[1].decode("utf-8", errors="replace")
        log_output = f"stdout:\n{stdout}\nstderr:\n{stderr}"
    else:
        log_output = logs.decode("utf-8", errors="replace")
    raise RuntimeError(
        "Kreuzberg did not become ready "
        f"after {startup_timeout_seconds} seconds.\n{log_output}"
    )


def get_postgres_url(
    container: LemmaPostgresContainer, database_name: Optional[str] = None
) -> str:
    """Helper to extract async database URL from container.

    ``database_name`` overrides the container's default database (used by the
    shared-Postgres model, where multiple xdist workers share one container
    but each connects to its own logical database).
    """
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    user = container.username
    password = container.password
    dbname = database_name or container.dbname
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"


def get_postgres_uri_from_another_container(
    container: LemmaPostgresContainer, database_name: str
) -> str:
    """A bare ``postgresql://`` URI for a container connecting from ANOTHER
    container (e.g. SuperTokens' ``POSTGRESQL_CONNECTION_URI``) on the SAME
    ``SHARED_E2E_NETWORK_NAME`` Docker network, not from the host pytest
    process.

    ``get_postgres_url()`` returns ``container.get_container_host_ip()``
    (``127.0.0.1``) plus the ``+asyncpg`` SQLAlchemy dialect suffix -- right
    for this process's own connections, wrong on every count for a sibling
    container: ``127.0.0.1`` inside a container means the container itself,
    SuperTokens' Java core doesn't understand a SQLAlchemy dialect string,
    and routing back out through a host-published port doesn't work either --
    ``host.docker.internal`` was tried first and confirmed broken on CI's
    native Linux runners: Postgres's ``-p 127.0.0.1::5432`` binding only
    accepts connections whose destination is literally ``127.0.0.1``, and
    traffic arriving via the docker0 bridge gateway (what ``host.docker.
    internal`` resolves to without Docker Desktop's Mac/Windows-only magic)
    has a different destination address, so it's refused before Postgres
    ever sees it -- passed locally for the same reason the other attempt did.

    Use the container's own name and internal port instead: Docker's
    embedded DNS resolves a container by name for any other container on the
    same user-defined network, and that traffic goes container-to-container
    directly, never touching the host's published-port machinery at all.
    ``container.container_id`` is a name, not a docker-assigned hex id, for
    every caller of this function -- see ``shared_postgres``.
    """
    return (
        f"postgresql://{container.username}:{container.password}"
        f"@{container.container_id}:5432/{database_name}"
    )


def create_postgres_database(
    container: LemmaPostgresContainer, database_name: str
) -> None:
    """Create a database in the running Postgres test container if it does not exist."""
    dsn = (
        f"host={container.get_container_host_ip()} "
        f"port={container.get_exposed_port(5432)} "
        f"user={container.username} "
        f"password={container.password} "
        f"dbname={container.dbname}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cur.fetchone() is None:
                # Two xdist workers can both find a shared database missing;
                # the one that loses the race finds it made.
                try:
                    cur.execute(f'CREATE DATABASE "{database_name}"')
                except psycopg.errors.UniqueViolation, psycopg.errors.DuplicateDatabase:
                    pass


def get_redis_url(container: LemmaDockerContainer, db: Optional[int] = None) -> str:
    """Helper to extract Redis URL from container.

    ``db`` overrides the connection's logical database index (used by the
    shared-Redis model, where multiple xdist workers share one Redis server
    but each connects to its own logical DB, 0-15 by default). Omit for the
    server's default DB 0.
    """
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    if db is None:
        return f"redis://{host}:{port}"
    return f"redis://{host}:{port}/{db}"


def get_supertokens_url(container: LemmaDockerContainer) -> str:
    """Helper to extract SuperTokens URL from container."""
    host = container.get_container_host_ip()
    port = container.get_exposed_port(3567)
    return f"http://{host}:{port}"


def get_kreuzberg_url(container: LemmaDockerContainer) -> str:
    """Helper to extract Kreuzberg URL from container."""
    host = container.get_container_host_ip()
    port = container.get_exposed_port(8000)
    return f"http://{host}:{port}"


def _reap_cli() -> None:
    """Entry point for ``make docker-reap`` — a manual, on-demand version of the
    same age-gated sweep that already runs automatically around e2e test
    sessions. Prints what it removed; safe to run anytime, including while
    another e2e/load-test run is active on the same machine, since it never
    touches anything still running."""
    containers = reap_stale_lemma_containers()
    volumes = reap_stale_lemma_volumes()
    if containers:
        print(f"Removed {len(containers)} stale container(s): {', '.join(containers)}")
    else:
        print("No stale containers to remove.")
    if volumes:
        print(f"Removed {len(volumes)} stale volume(s): {', '.join(volumes)}")
    else:
        print("No stale lemma-vol-* volumes to remove.")
    print(
        "Note: this does not prune anonymous/dangling volumes left by older "
        "runs before the -v fix, nor unrelated Docker images/build cache — "
        "run `docker volume prune` / `docker system prune` separately for those."
    )


if __name__ == "__main__":
    _reap_cli()
