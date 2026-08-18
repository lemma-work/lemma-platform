import os
import socket
import subprocess
import time
from contextlib import contextmanager
from typing import Generator, Optional

import psycopg

# Use same images as docker-compose.yml for consistency. pgvector 0.8.3 is
# required for the halfvec vector indexes the search service now builds.
POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg15"
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
MINIO_IMAGE = "quay.io/minio/minio:latest"
# Local-only credentials for a throwaway test container.
MINIO_ROOT_USER = "minioadmin"
MINIO_ROOT_PASSWORD = "minioadmin"
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
            DOCKER_LABEL,
            "-p",
            f"127.0.0.1::{self.internal_port}",
        ]
        for name, value in self._env.items():
            command.extend(["-e", f"{name}={value}"])
        command.extend(self._extra_run_args)
        command.append(self.image)
        # After the image, so this is the container's command rather than a
        # `docker run` flag. MinIO needs `server /data`; images with a usable
        # ENTRYPOINT supply nothing here.
        command.extend(self._command)

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
def get_postgres_container() -> Generator[LemmaPostgresContainer, None, None]:
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
def get_minio_container() -> Generator[LemmaDockerContainer, None, None]:
    """Start MinIO, so multipart uploads are tested against a real part-size rule.

    The local filesystem store accepts any chunk size. GCS and S3 reject a
    non-final part under 5 MiB, and a 1 MiB chunk shipped and broke every
    datastore file upload over 1 MiB in production. MinIO enforces the same
    minimum, so it reproduces that failure and proves the fix.
    """
    container = (
        LemmaDockerContainer(MINIO_IMAGE, 9000)
        .with_env("MINIO_ROOT_USER", MINIO_ROOT_USER)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_ROOT_PASSWORD)
    )
    container.with_command("server", "/data")
    with container as minio:
        _wait_for_tcp(minio, 9000, _env_int("MINIO_STARTUP_TIMEOUT_SECONDS", 120))
        yield minio


@contextmanager
def get_supertokens_container() -> Generator[LemmaDockerContainer, None, None]:
    """
    Starts a SuperTokens container with in-memory SQLite (default).
    """
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


def start_shared_postgres(name: str) -> LemmaPostgresContainer:
    """Start ONE named Postgres container and return a reference to it.

    Mirrors ``start_shared_kreuzberg``: one Postgres *server process* shared
    across all xdist workers (each worker still gets its own logical
    database inside it via ``create_postgres_database``), instead of one
    full container per worker. Not auto-stopped; the last worker out calls
    ``remove_named_container`` (and the label-based prune sweeps any
    straggler).
    """
    # Clear any straggler with this name from a previously crashed run.
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    container = (
        LemmaPostgresContainer()
        .with_env("POSTGRES_USER", POSTGRES_USER)
        .with_env("POSTGRES_PASSWORD", POSTGRES_PASSWORD)
        .with_env("POSTGRES_DB", POSTGRES_DB)
        .with_run_args("--name", name)
    )
    container.__enter__()  # start detached; intentionally no matching __exit__
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
    if worker_id == "master":
        with get_postgres_container() as postgres:
            yield postgres
        return

    from pathlib import Path

    from filelock import FileLock

    root = Path(basetemp_parent)
    lock = FileLock(str(root / "postgres.lock"))
    refs_file = root / "postgres_refs.txt"

    with lock:
        refs = int(refs_file.read_text()) if refs_file.exists() else 0
        if refs == 0:
            start_shared_postgres(SHARED_POSTGRES_NAME)
        refs_file.write_text(str(refs + 1))

    container = LemmaPostgresContainer()
    container.container_id = SHARED_POSTGRES_NAME

    try:
        yield container
    finally:
        with lock:
            refs = (int(refs_file.read_text()) if refs_file.exists() else 1) - 1
            refs_file.write_text(str(refs))
            if refs <= 0:
                remove_named_container(SHARED_POSTGRES_NAME)
                refs_file.unlink(missing_ok=True)


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
