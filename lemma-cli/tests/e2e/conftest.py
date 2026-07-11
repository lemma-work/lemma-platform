from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

# NOTE: httpx and psycopg are imported lazily inside the fixtures that use them,
# so a unit-only run (`pytest -m "not e2e"`) never requires the e2e-only dev deps
# to be importable just because this conftest is collected.

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "lemma-backend"

POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg15"
REDIS_IMAGE = "redis/redis-stack:7.2.0-v19"
SUPERTOKENS_IMAGE = "docker.io/supertokens/supertokens-postgresql:11.4.5"

POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker_run(image, internal_port, env=None):
    cmd = ["docker", "run", "-d", "--label", "lemma.cli.e2e=true",
           "-p", f"127.0.0.1::{internal_port}"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd.append(image)
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _get_port(container_id, internal_port):
    result = subprocess.run(
        ["docker", "port", container_id, f"{internal_port}/tcp"],
        check=True, capture_output=True, text=True
    )
    return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _wait_tcp(host, port, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"TCP {host}:{port} not ready after {timeout}s")


def _wait_http(url, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"HTTP {url} not ready after {timeout}s")


def _wait_postgres(host, port, timeout=120):
    import psycopg

    dsn = f"host={host} port={port} user={POSTGRES_USER} password={POSTGRES_PASSWORD} dbname={POSTGRES_DB}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, autocommit=True):
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Postgres at {host}:{port} not ready after {timeout}s")


# --- Session-scoped: Docker containers --------------------------------------

@pytest.fixture(scope="session")
def postgres_container():
    cid = _docker_run(POSTGRES_IMAGE, 5432, {
        "POSTGRES_USER": POSTGRES_USER,
        "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
        "POSTGRES_DB": POSTGRES_DB,
    })
    port = _get_port(cid, 5432)
    _wait_postgres("127.0.0.1", port)
    # The squashed baseline migration creates tables with pgvector columns, so
    # the `vector` extension must exist before `alembic upgrade head` runs. The
    # pgvector image ships the extension files; this just enables it in the DB.
    _ensure_vector_extension(cid)
    yield {"host": "127.0.0.1", "port": port, "cid": cid}
    subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)


def _ensure_vector_extension(container_id: str) -> None:
    result = subprocess.run(
        ["docker", "exec", container_id, "psql", "-U", POSTGRES_USER, "-d", POSTGRES_DB,
         "-c", "CREATE EXTENSION IF NOT EXISTS vector"],
        check=True, capture_output=True, text=True,
    )
    del result


@pytest.fixture(scope="session")
def redis_container():
    cid = _docker_run(REDIS_IMAGE, 6379)
    port = _get_port(cid, 6379)
    _wait_tcp("127.0.0.1", port)
    yield {"host": "127.0.0.1", "port": port, "cid": cid}
    subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)


@pytest.fixture(scope="session")
def supertokens_container():
    cid = _docker_run(SUPERTOKENS_IMAGE, 3567)
    port = _get_port(cid, 3567)
    _wait_http(f"http://127.0.0.1:{port}/hello")
    yield {"host": "127.0.0.1", "port": port, "cid": cid}
    subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)


# --- Session-scoped: backend uvicorn subprocess -----------------------------

def _run_migrations(python_bin: str, env: dict[str, str]) -> None:
    """Run `alembic upgrade head` against the test DB using the backend venv.

    The backend's own e2e uses in-process SQLAlchemy create_all; that's not
    reachable from the CLI venv, so we drive alembic (the canonical migration
    path) as a subprocess with the same env used for the uvicorn server.
    """
    result = subprocess.run(
        [python_bin, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed — the backend DB schema could not be "
            "created.\n"
            f"Check that lemma-backend deps are installed "
            f"(cd lemma-backend && uv sync).\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def backend_server(postgres_container, redis_container, supertokens_container):
    """Start lemma-backend as a subprocess uvicorn server.

    Why subprocess (not in-process uvicorn): the Lemma SDK makes real TCP HTTP
    calls; it cannot use ASGI transport. The backend's own e2e tests use in-process
    uvicorn + httpx ASGITransport, but that only works for async Python tests that
    call the ASGI app directly.
    """
    port = _free_port()
    db_url = (
        f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{postgres_container['host']}:{postgres_container['port']}/{POSTGRES_DB}"
    )
    redis_url = f"redis://{redis_container['host']}:{redis_container['port']}"
    st_url = f"http://{supertokens_container['host']}:{supertokens_container['port']}"

    # Use the backend's own venv Python if available (avoids dependency mismatches)
    venv_python = BACKEND_ROOT / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable

    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_ROOT),
        "ENVIRONMENT": "testing",
        "DATABASE_URL": db_url,
        "DATASTORE_DATABASE_URL": db_url,
        "REDIS_URL": redis_url,
        "SUPERTOKENS_CORE_URL": st_url,
        "SUPERTOKENS_ENV": "testing",
        "DEBUG": "true",
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_FILE_STORAGE_ROOT": f"/tmp/lemma-cli-e2e-files-{port}",
        "LOCAL_OBJECT_STORAGE_ROOT": f"/tmp/lemma-cli-e2e-objects-{port}",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": f"/tmp/lemma-cli-e2e-emails-{port}",
        "AGENTBOX_API_KEY": "test-key",
        "AGENTBOX_API_URL": "http://localhost:9999",
    }

    # Apply the DB schema before starting the server. The uvicorn lifespan does
    # not auto-migrate, so without this every API call 500s on missing tables.
    # alembic reads DATABASE_URL from `env` (overriding the backend's .env).
    _run_migrations(python_bin, env)

    # The scheduler API (app.scheduler:app) is a separate FastAPI process that
    # the backend calls to register cron jobs when creating TIME schedules. The
    # backend's own e2e stubs it in-process; here we run it as a sibling uvicorn
    # subprocess (Redis-backed, no streaq worker needed for CRUD-only flows).
    sched_port = _free_port()
    env["SCHEDULER_API_URL"] = f"http://127.0.0.1:{sched_port}"
    sched_proc = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "app.scheduler:app",
         "--host", "127.0.0.1", "--port", str(sched_port), "--log-level", "warning"],
        cwd=str(BACKEND_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    procs = [sched_proc]
    try:
        _wait_http(f"http://127.0.0.1:{sched_port}/health", timeout=60)

        proc = subprocess.Popen(
            [python_bin, "-m", "uvicorn", "app.app:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(BACKEND_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.insert(0, proc)

        base_url = f"http://127.0.0.1:{port}"
        _wait_http(f"{base_url}/health", timeout=90)
    except RuntimeError:
        for p in procs:
            p.terminate()
        # Surface whatever output we have to diagnose startup failures.
        out = ""
        for p in procs:
            try:
                o, _ = p.communicate(timeout=5)
                out += o
            except subprocess.TimeoutExpired:
                p.kill()
        pytest.fail(
            f"Backend/scheduler server did not start in time.\n"
            f"Check that lemma-backend deps are installed (cd lemma-backend && uv sync).\n"
            f"Server output:\n{out}"
        )

    yield {"base_url": base_url, "port": port}

    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


# --- Function-scoped: test user ---------------------------------------------

@pytest.fixture
def test_user(backend_server):
    """Create a unique test user via SuperTokens signup and return their token."""
    import httpx

    email = f"cli-e2e-{uuid4().hex[:10]}@example.com"
    password = "TestPassword@123"
    base_url = backend_server["base_url"]

    with httpx.Client(base_url=base_url, timeout=30) as client:
        resp = client.post(
            "/st/auth/signup",
            json={
                "formFields": [
                    {"id": "email", "value": email},
                    {"id": "password", "value": password},
                ]
            },
        )
        assert resp.status_code == 200, f"Signup failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "OK", f"Signup status not OK: {data}"

        token = resp.headers.get("st-access-token") or resp.cookies.get("sAccessToken")
        assert token, f"No access token in response headers: {dict(resp.headers)}"

        yield {"email": email, "token": token, "id": data["user"]["id"]}


@pytest.fixture
def test_org(backend_server, test_user):
    """Create a fresh organization for the test user."""
    import httpx

    base_url = backend_server["base_url"]
    org_name = f"CLI E2E Org {uuid4().hex[:8]}"

    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {test_user['token']}"},
        timeout=30,
    ) as client:
        resp = client.post("/organizations", json={"name": org_name})
        assert resp.status_code in (200, 201), f"Org create failed: {resp.text}"
        yield resp.json()


@pytest.fixture
def test_pod(backend_server, test_user, test_org):
    """Create a fresh pod for the test org.

    Retries on a transient 403 "not a member": across the subprocess boundary
    the backend's Redis event consumers run concurrently with this process, so
    org-membership propagation can occasionally lag a few ms behind the org
    creation response. The backend's own in-process e2e never sees this (one
    event loop); this bounded retry closes that gap without masking real errors.
    """
    import httpx
    import time

    base_url = backend_server["base_url"]
    pod_name = f"cli-e2e-pod-{uuid4().hex[:8]}"
    org_id = test_org["id"]

    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {test_user['token']}"},
        timeout=30,
    ) as client:
        last_body = ""
        for attempt in range(3):
            resp = client.post(
                "/pods",
                json={"name": pod_name, "organization_id": org_id},
            )
            if resp.status_code in (200, 201):
                yield resp.json()
                return
            last_body = resp.text
            is_membership_race = (
                resp.status_code == 403 and "member" in resp.text.lower()
            )
            if not is_membership_race or attempt == 2:
                break
            time.sleep(0.25)
        assert False, f"Pod create failed: {last_body}"


@pytest.fixture(autouse=True)
def truncate_tables(postgres_container):
    """Truncate all application tables after each test for isolation.

    A short settle delay lets the backend's Redis event consumers (running in
    the separate uvicorn subprocess) finish draining in-flight events BEFORE we
    truncate. Without it, a consumer can write a row just after TRUNCATE and
    leak state into the next test. The backend's own in-process e2e doesn't need
    this (one event loop = no concurrency); the subprocess boundary does.
    """
    yield
    import psycopg

    time.sleep(0.15)
    dsn = (
        f"host={postgres_container['host']} port={postgres_container['port']} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} dbname={POSTGRES_DB}"
    )
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
                tables = [
                    row[0] for row in cur.fetchall()
                    if row[0] not in ("alembic_version",)
                ]
                if tables:
                    quoted = ", ".join(f'"{t}"' for t in tables)
                    cur.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
    except Exception:
        pass  # Don't fail the test if truncation fails


def pytest_collection_modifyitems(items):
    """Auto-mark all tests in the e2e/ directory."""
    for item in items:
        if "e2e" in str(item.fspath).split("/"):
            item.add_marker(pytest.mark.e2e)
