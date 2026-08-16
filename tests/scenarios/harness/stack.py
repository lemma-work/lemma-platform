"""Boot the system under test, and hand back a URL.

This suite is black box: it drives Lemma over a real socket, exactly as the
frontend, the CLI and both SDKs do. Nothing here imports ``lemma-backend``'s
application code, and ``conftest.py`` asserts that.

What gets started, once per session:

* Postgres (with ``pgvector`` enabled — the baseline migration needs it),
  Redis, and SuperTokens, as containers.
* ``alembic upgrade head`` against that database, through the backend's own
  virtualenv. The uvicorn lifespan does not migrate, so without this every call
  fails on missing tables.
* The scheduler API, which the backend calls when a time schedule is created.
* The backend itself, under uvicorn.

Session-scoped on purpose. Booting this takes tens of seconds, so a
function-scoped stack would make the suite unusable. The cost of sharing it is
that scenarios must not assume an empty world: every scenario creates its own
people, organization and pod with unique names, and asserts on what it created
rather than on totals. ``World`` makes that the easy path.

Generalised from ``lemma-cli/tests/e2e/conftest.py``, which already does all of
this for the CLI suite.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "lemma-backend"

POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg15"
REDIS_IMAGE = "redis:7.4-alpine"
SUPERTOKENS_IMAGE = "docker.io/supertokens/supertokens-postgresql:11.4.5"

POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"

CONTAINER_LABEL = "lemma.scenarios=true"


class StackError(RuntimeError):
    """The system under test could not be started."""


@dataclass(frozen=True, slots=True)
class Stack:
    """A running Lemma, addressable over HTTP."""

    base_url: str
    redis_url: str
    database_url: str
    log_path: str = ""

    def tail(self, lines: int = 80, *, match: str = "") -> str:
        """The end of the server and worker log.

        Worth having because the most confusing failure this suite produces is a
        scenario that times out waiting for background work: the API said 200,
        nothing happened, and the reason is in the worker's log rather than in
        anything the scenario can see.
        """
        if not self.log_path:
            return "(no log; the stack was not started by this process)"
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as handle:
                content = handle.readlines()
        except OSError as error:
            return f"(could not read {self.log_path}: {error})"
        if match:
            content = [line for line in content if match in line]
        return "".join(content[-lines:])


# --- container plumbing -----------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def require_docker() -> None:
    """Fail early and legibly when Docker is not usable.

    Without this the first failure is a ``CalledProcessError`` from ``docker
    run`` several frames deep, which reads as a bug in the suite rather than as
    "start Docker".
    """
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError as error:
        raise StackError(
            "Docker is not installed, and the scenario suite needs it to run "
            "Postgres, Redis and SuperTokens.\n"
            "Point the suite at an already-running Lemma instead: "
            "pytest --base-url http://localhost:8000"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise StackError("`docker info` timed out; is the daemon healthy?") from error
    if result.returncode != 0:
        raise StackError(
            "Docker is installed but not running, and the scenario suite needs "
            "it to run Postgres, Redis and SuperTokens.\n"
            f"`docker info` said: {(result.stderr or result.stdout).strip()[:500]}\n"
            "Start Docker, or point the suite at an already-running Lemma: "
            "pytest --base-url http://localhost:8000"
        )


def _docker_run(image: str, internal_port: int, env: dict[str, str] | None = None) -> str:
    command = [
        "docker", "run", "-d",
        "--label", CONTAINER_LABEL,
        "-p", f"127.0.0.1::{internal_port}",
    ]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    command.append(image)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise StackError(
            f"could not start {image}: {(result.stderr or result.stdout).strip()[:500]}"
        )
    return result.stdout.strip()


def _mapped_port(container_id: str, internal_port: int) -> int:
    result = subprocess.run(
        ["docker", "port", container_id, f"{internal_port}/tcp"],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _remove(container_id: str) -> None:
    subprocess.run(["docker", "rm", "-f", container_id], check=False, capture_output=True)


def _wait_tcp(host: str, port: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise StackError(f"nothing listening on {host}:{port} after {timeout}s")


def _wait_http(url: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    raise StackError(f"{url} did not become ready within {timeout}s")


def _wait_postgres(host: str, port: int, timeout: float = 120) -> None:
    import psycopg

    dsn = (
        f"host={host} port={port} user={POSTGRES_USER} "
        f"password={POSTGRES_PASSWORD} dbname={POSTGRES_DB}"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, autocommit=True):
                return
        except Exception:
            time.sleep(0.5)
    raise StackError(f"postgres at {host}:{port} not ready after {timeout}s")


# --- the stack --------------------------------------------------------------


def _backend_python() -> str:
    """The backend's own interpreter, so its dependencies resolve.

    Per-worktree: the main checkout's virtualenv is routinely a different set of
    pinned versions, and running migrations under the wrong one fails in ways
    that look like schema bugs.
    """
    candidate = BACKEND_ROOT / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _environment(*, port: int, database_url: str, redis_url: str, supertokens_url: str) -> dict[str, str]:
    scratch = Path(tempfile.gettempdir()) / f"lemma-scenarios-{port}"
    return {
        **os.environ,
        "PYTHONPATH": str(BACKEND_ROOT),
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        "API_URL": f"http://127.0.0.1:{port}",
        "FRONTEND_URL": f"http://127.0.0.1:{port}",
        "AUTH_FRONTEND_URL": f"http://127.0.0.1:{port}",
        "DATABASE_URL": database_url,
        "DATASTORE_DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "SUPERTOKENS_CORE_URL": supertokens_url,
        "SUPERTOKENS_ENV": "testing",
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_FILE_STORAGE_ROOT": str(scratch / "files"),
        "LOCAL_OBJECT_STORAGE_ROOT": str(scratch / "objects"),
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": str(scratch / "email"),
        # Signup is a step in most journeys, so the protections that exist to
        # slow down real abuse would otherwise dominate the suite. They are
        # exercised deliberately by their own scenarios, not incidentally here.
        "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED": "false",
        "AUTH_EMAIL_VERIFICATION_REQUIRED": "false",
        "AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED": "false",
        "AUTH_ABUSE_PROTECTION_ENABLED": "false",
        "AUTH_ALTCHA_ENABLED": "false",
        # The one substitution the stack makes, and it is deliberate: agent runs
        # use a deterministic scripted model rather than a real provider. It is a
        # supported setting (`e2e_llm_mode`), not a patched-in fake, so the code
        # path under test is the production one all the way to the model boundary.
        # Without it every agent scenario needs an API key and returns something
        # different each run.
        "E2E_LLM_MODE": "mock",
        # None of these three are ever used to reach a provider — in mock mode
        # the model is swapped for a scripted one before any call is made. They
        # have to be *present* because building the system runtime profile
        # refuses up front when the server has no key and no model names, which
        # is the right behaviour and happens before the swap.
        "LEMMA_OPENAI_API_KEY": "scenarios-mock-key-not-used",
        "LEMMA_OPENAI_MODEL_NAMES": "gpt-4o-mini",
        "LEMMA_OPENAI_DEFAULT_MODEL": "gpt-4o-mini",
        # Needed before a sandbox can be provisioned at all. Provisioning
        # itself also needs the workspace and function images, which the fast
        # lane does not build — see the `sandbox` marker in pyproject.toml.
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": "scenarios-runtime-credential-key-32b",
    }


def _migrate(python_bin: str, env: dict[str, str]) -> None:
    result = subprocess.run(
        [python_bin, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise StackError(
            "alembic upgrade head failed — the schema could not be created.\n"
            "Check the backend's dependencies are installed "
            "(cd lemma-backend && uv sync).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def start_stack():
    """Start everything and yield a :class:`Stack`. Generator, for a fixture."""
    require_docker()

    containers: list[str] = []
    processes: list[subprocess.Popen] = []
    log_path = Path(tempfile.gettempdir()) / f"lemma-scenarios-{os.getpid()}.log"
    log = open(log_path, "w+", encoding="utf-8")

    try:
        postgres = _docker_run(POSTGRES_IMAGE, 5432, {
            "POSTGRES_USER": POSTGRES_USER,
            "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
            "POSTGRES_DB": POSTGRES_DB,
        })
        containers.append(postgres)
        postgres_port = _mapped_port(postgres, 5432)
        _wait_postgres("127.0.0.1", postgres_port)
        subprocess.run(
            ["docker", "exec", postgres, "psql", "-U", POSTGRES_USER, "-d",
             POSTGRES_DB, "-c", "CREATE EXTENSION IF NOT EXISTS vector"],
            check=True, capture_output=True, text=True,
        )

        redis = _docker_run(REDIS_IMAGE, 6379)
        containers.append(redis)
        redis_port = _mapped_port(redis, 6379)
        _wait_tcp("127.0.0.1", redis_port)

        supertokens = _docker_run(SUPERTOKENS_IMAGE, 3567)
        containers.append(supertokens)
        supertokens_port = _mapped_port(supertokens, 3567)
        _wait_http(f"http://127.0.0.1:{supertokens_port}/hello")

        port = _free_port()
        database_url = (
            f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{postgres_port}/{POSTGRES_DB}"
        )
        redis_url = f"redis://127.0.0.1:{redis_port}"
        env = _environment(
            port=port,
            database_url=database_url,
            redis_url=redis_url,
            supertokens_url=f"http://127.0.0.1:{supertokens_port}",
        )

        python_bin = _backend_python()
        _migrate(python_bin, env)

        # No scheduler sidecar. APScheduler and `app/scheduler.py` were deleted
        # in #362; time schedules are driven from the worker now. Booting one
        # here is what `lemma-cli/tests/e2e/conftest.py` still does, which is
        # why that suite fails before its first assertion — see DEV-OPS-001.
        processes.append(subprocess.Popen(
            [python_bin, "-m", "uvicorn", "app.app:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(BACKEND_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        ))
        base_url = f"http://127.0.0.1:{port}"
        _wait_http(f"{base_url}/health", timeout=120)

        # The worker. Agent runs, workflow resumes, scheduled fires and document
        # processing are all queued rather than done in the request, so without
        # this the API accepts the work and nothing ever picks it up — which
        # looks exactly like a product bug from a scenario's point of view.
        processes.append(subprocess.Popen(
            [python_bin, "-m", "app.worker"],
            cwd=str(BACKEND_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        ))

        yield Stack(
            base_url=base_url,
            redis_url=redis_url,
            database_url=database_url,
            log_path=str(log_path),
        )

    except StackError as error:
        log.seek(0)
        output = log.read()
        raise StackError(f"{error}\n\nServer output:\n{output}") from error
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for container_id in containers:
            _remove(container_id)
        log.close()
