import os
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator, Optional
from uuid import uuid4

import psycopg

# Use same images as docker-compose.yml for consistency. pgvector 0.8.3 is
# required for the halfvec vector indexes the search service now builds.
POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg15"
REDIS_IMAGE = "redis/redis-stack:7.2.0-v19"
SUPERTOKENS_IMAGE = "docker.io/supertokens/supertokens-postgresql:11.4.5"
# The project moved orgs (kreuzberg-dev -> xberg-io); the old
# ghcr.io/kreuzberg-dev/kreuzberg:4.9.9 tag now 404s ("manifest unknown"). This
# is the same 4.9.9 on the current public namespace (matches docker-compose.yml).
# The -core image is ~580MB and fetches OCR models on first use.
KREUZBERG_IMAGE = "ghcr.io/xberg-io/kreuzberg:4.9.9-core"
POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"
DOCKER_LABEL = "lemma.e2e=true"


@dataclass
class LemmaDockerNetwork:
    name: str = field(default_factory=lambda: f"lemma-e2e-{uuid4().hex[:12]}")

    def __enter__(self) -> "LemmaDockerNetwork":
        subprocess.run(
            ["docker", "network", "create", self.name],
            check=True,
            capture_output=True,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        subprocess.run(
            ["docker", "network", "rm", self.name],
            check=False,
            capture_output=True,
        )


class LemmaDockerContainer:
    def __init__(self, image: str, internal_port: int) -> None:
        self.image = image
        self.internal_port = internal_port
        self.container_id: str | None = None
        self._network: LemmaDockerNetwork | None = None
        self._env: dict[str, str] = {}
        self._extra_run_args: list[str] = []

    def with_env(self, name: str, value: str) -> "LemmaDockerContainer":
        self._env[name] = value
        return self

    def with_run_args(self, *args: str) -> "LemmaDockerContainer":
        """Append extra ``docker run`` flags (e.g. ``--memory``, ``--restart``)."""
        self._extra_run_args.extend(args)
        return self

    def with_network(self, network: LemmaDockerNetwork) -> "LemmaDockerContainer":
        self._network = network
        return self

    def __enter__(self) -> "LemmaDockerContainer":
        command = [
            "docker",
            "run",
            "-d",
            "--label",
            DOCKER_LABEL,
            "-p",
            f"127.0.0.1::{self.internal_port}",
        ]
        if self._network is not None:
            command.extend(["--network", self._network.name])
        for name, value in self._env.items():
            command.extend(["-e", f"{name}={value}"])
        command.extend(self._extra_run_args)
        command.append(self.image)

        result = subprocess.run(command, check=True, capture_output=True, text=True)
        self.container_id = result.stdout.strip()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.container_id:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
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


class LemmaPostgresContainer(LemmaDockerContainer):
    username = POSTGRES_USER
    password = POSTGRES_PASSWORD
    dbname = POSTGRES_DB

    def __init__(self) -> None:
        super().__init__(POSTGRES_IMAGE, 5432)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _prune_e2e_containers() -> None:
    """Best-effort cleanup for stale E2E containers."""
    if os.getenv("TESTCONTAINERS_PRUNE_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return

    container_ids = []
    list_result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={DOCKER_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if list_result.returncode != 0:
        return
    container_ids.extend(cid for cid in list_result.stdout.splitlines() if cid.strip())

    if not container_ids:
        return

    subprocess.run(["docker", "rm", "-f", *container_ids], check=False, capture_output=True)


def _wait_for_tcp(container: LemmaDockerContainer, port: int, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    host = container.get_container_host_ip()
    exposed_port = int(container.get_exposed_port(port))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, exposed_port), timeout=2):
                return
        except OSError:
            time.sleep(0.5)
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
            time.sleep(0.5)
    logs = container.get_logs().decode("utf-8", errors="replace")
    raise RuntimeError(f"Postgres did not become ready in time.\n{logs}")


@contextmanager
def get_test_network() -> Generator[LemmaDockerNetwork, None, None]:
    """
    Creates a Docker network for test containers to communicate.
    """
    _prune_e2e_containers()
    with LemmaDockerNetwork() as network:
        try:
            yield network
        finally:
            _prune_e2e_containers()


@contextmanager
def get_postgres_container(
    network: Optional[LemmaDockerNetwork] = None,
) -> Generator[LemmaPostgresContainer, None, None]:
    """
    Starts a PostgreSQL container and yields it.
    Can be used in pytest fixtures with scope="session".
    """
    container = (
        LemmaPostgresContainer()
        .with_env("POSTGRES_USER", POSTGRES_USER)
        .with_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        .with_env("POSTGRES_DB", POSTGRES_DB)
    )
    if network:
        container.with_network(network)

    with container as postgres:
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
def get_supertokens_container() -> Generator[LemmaDockerContainer, None, None]:
    """
    Starts a SuperTokens container with in-memory SQLite (default).
    """
    container = LemmaDockerContainer(SUPERTOKENS_IMAGE, 3567)

    with container as st:
        # Wait for SuperTokens to be ready by polling the health endpoint
        import time
        import urllib.request
        import urllib.error

        host = st.get_container_host_ip()
        port = st.get_exposed_port(3567)
        health_url = f"http://{host}:{port}/hello"

        startup_timeout_seconds = _env_int("SUPERTOKENS_STARTUP_TIMEOUT_SECONDS", 120)
        poll_interval_seconds = _env_int("SUPERTOKENS_STARTUP_POLL_SECONDS", 1)
        max_retries = max(1, startup_timeout_seconds // max(1, poll_interval_seconds))
        for i in range(max_retries):
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    if response.status == 200:
                        break
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

        yield st


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
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    container = LemmaDockerContainer(KREUZBERG_IMAGE, 8000).with_run_args(
        "--name", name, "--restart", "unless-stopped"
    )
    container.__enter__()  # start detached; intentionally no matching __exit__
    _wait_for_kreuzberg_ready(container)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(8000)
    return f"http://{host}:{port}"


def remove_named_container(name: str) -> None:
    """Force-remove a container by name (best effort)."""
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


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
    lock = FileLock(str(root / "kreuzberg.lock"))
    url_file = root / "kreuzberg_url.txt"
    refs_file = root / "kreuzberg_refs.txt"

    with lock:
        if url_file.exists():
            url = url_file.read_text().strip()
        else:
            url = start_shared_kreuzberg(SHARED_KREUZBERG_NAME)
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
                remove_named_container(SHARED_KREUZBERG_NAME)
                url_file.unlink(missing_ok=True)
                refs_file.unlink(missing_ok=True)


def get_postgres_url(container: LemmaPostgresContainer) -> str:
    """Helper to extract async database URL from container."""
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    user = container.username
    password = container.password
    dbname = container.dbname
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"


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
                cur.execute(f'CREATE DATABASE "{database_name}"')


def get_redis_url(container: LemmaDockerContainer) -> str:
    """Helper to extract Redis URL from container."""
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    return f"redis://{host}:{port}"


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
