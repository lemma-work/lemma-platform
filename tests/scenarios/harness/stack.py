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

from harness.credentials import load_deployment_env

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "lemma-backend"

POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg15"
REDIS_IMAGE = "redis:7.4-alpine"
SUPERTOKENS_IMAGE = "docker.io/supertokens/supertokens-postgresql:11.4.5"

POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"

CONTAINER_LABEL = "lemma.scenarios=true"

#: Sandbox images, built by `make scenarios-images`. Plain tags rather than the
#: content-addressed names the backend's own e2e uses: those are rebuilt
#: whenever anything under the repo changes, which is right for a release gate
#: and wrong for a suite meant to be run constantly.
WORKSPACE_IMAGE = "lemma-workspace:scenarios"
FUNCTION_IMAGE = "lemma-function:scenarios"

#: What the stack claims its public address is.
#:
#: Surfaces refuse to be created unless `API_URL` is public HTTPS, because that
#: is where a platform would deliver webhooks. Nothing in this suite waits for a
#: platform to call in — scenarios deliver webhooks themselves — so the value
#: only has to *look* public.
#:
#: The cost is that any absolute URL the product hands back (a signed bundle
#: download, for one) points here rather than at the port the server is really
#: on. `ApiDriver` rewrites those back; see `drivers/api.py`.
PUBLIC_API_URL = "https://scenarios.lemma.example"

#: The Svix signing secret the stack runs with, and the domain its email
#: surfaces get their addresses under. Base64 after the `whsec_` prefix, which
#: is the shape Svix issues and the verifier decodes.
RESEND_WEBHOOK_SECRET = "whsec_c2NlbmFyaW9zLXJlc2VuZC1zaWduaW5nLXNlY3JldA=="
RESEND_INBOUND_DOMAIN = "scenarios.lemma.example"


def sandbox_images_present() -> bool:
    """Whether the sandbox images have been built.

    Used to skip the sandbox lane with a message that says what to run, rather
    than failing deep inside a provisioning call.
    """
    for image in (WORKSPACE_IMAGE, FUNCTION_IMAGE):
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True
        )
        if result.returncode != 0:
            return False
    return True


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


def _coverage_environment() -> dict[str, str]:
    """Instrument the backend and worker subprocesses, when asked.

    Off by default — measuring costs runtime and this suite is meant to be run
    constantly. `SCENARIOS_COVERAGE=1` turns it on, and `make scenarios-coverage`
    does the whole cycle: erase, run, combine, report.

    The backend's own `sitecustomize.py` calls `coverage.process_startup()` when
    it sees `COVERAGE_PROCESS_START`, and `PYTHONPATH` already points at the
    backend root, so nothing else has to be arranged.
    """
    if os.getenv("SCENARIOS_COVERAGE") != "1":
        return {}
    return {
        "COVERAGE_PROCESS_START": str(BACKEND_ROOT / ".coveragerc"),
        "COVERAGE_FILE": str(BACKEND_ROOT / ".coverage"),
    }


def _environment(*, port: int, database_url: str, redis_url: str, supertokens_url: str) -> dict[str, str]:
    scratch = Path(tempfile.gettempdir()) / f"lemma-scenarios-{port}"
    return {
        **_coverage_environment(),
        # The deployment's own configuration — but only when the live lane asks
        # for it. See `_deployment_settings`.
        **_deployment_settings(),
        **os.environ,
        "PYTHONPATH": str(BACKEND_ROOT),
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        # Deliberately a public-looking HTTPS URL rather than the loopback one
        # the server actually listens on. Surfaces refuse to be created unless
        # `API_URL` is public HTTPS, because that is where a platform would
        # deliver webhooks — and nothing in this suite waits for a platform to
        # call us: scenarios deliver the webhook themselves. Everything that
        # genuinely has to reach the running server (sandbox callbacks, the
        # function gateway) is pointed at the real host separately, below.
        "API_URL": PUBLIC_API_URL,
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
        # `real` runs agents on the provider the deployment is configured with,
        # which is what the live lane wants and what `make scenarios-live` sets.
        # The default stays deterministic: a suite on every push must not depend
        # on a model answering the same way twice.
        "E2E_LLM_MODE": os.getenv("SCENARIOS_LLM_MODE", "mock"),
        # Off, so surface scenarios can deliver webhooks and see them arrive.
        # The live lane sets it: a real bot on a runner with no public address
        # has no other way to receive, and there is no webhook to deliver.
        "ENABLE_TELEGRAM_POLLING_MODE": os.getenv(
            "SCENARIOS_TELEGRAM_POLLING", "false"
        ),
        "ENABLE_SLACK_SOCKET_MODE": "false",
        # Email surfaces. A Resend inbound webhook is Svix-signed, so without a
        # secret the endpoint answers 503 and no email scenario can run at all;
        # this is a well-formed throwaway, and scenarios sign with it exactly as
        # Resend would. The domain is what gives each surface its own address.
        "RESEND_WEBHOOK_SECRET": RESEND_WEBHOOK_SECRET,
        "RESEND_INBOUND_DOMAIN": RESEND_INBOUND_DOMAIN,
        "RESEND_API_KEY": "re_scenarios_not_a_real_key",
        # The self-hosted posture. Off in production so an org admin cannot
        # point a connector at the cloud metadata service; on here so a
        # connector can target the fake provider this suite runs on loopback.
        # Nothing is lost by flipping it: the guard's default-off behaviour is
        # covered directly by `app/core/tests/unit/test_url_guard.py`, which
        # asserts the refusal reason for loopback, private and link-local
        # addresses. What this suite adds is the lifecycle *around* it.
        "CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS": "true",
        # Placeholders, and only where the deployment configured nothing. In
        # mock mode none of them reaches a provider — the model is swapped for a
        # scripted one before any call is made — but they have to be *present*,
        # because building the system runtime profile refuses up front when the
        # server has no key and no model names. A deployment with real settings
        # keeps them, which is what lets the live lane use the real model.
        "LEMMA_OPENAI_API_KEY": _configured_or(
            "LEMMA_OPENAI_API_KEY", "scenarios-mock-key-not-used"
        ),
        "LEMMA_OPENAI_MODEL_NAMES": _configured_or(
            "LEMMA_OPENAI_MODEL_NAMES", "gpt-4o-mini"
        ),
        "LEMMA_OPENAI_DEFAULT_MODEL": _configured_or(
            "LEMMA_OPENAI_DEFAULT_MODEL", "gpt-4o-mini"
        ),
        # Needed before a sandbox can be provisioned at all.
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": "scenarios-runtime-credential-key-32b",
        # Sandboxes run as local Docker containers. The images are built by
        # `make scenarios-images`; the `sandbox` marker keeps the scenarios that
        # need them out of the fast lane, but the configuration is always
        # present so nothing has to be re-plumbed to run that lane.
        "WORKSPACE_PROVIDER": "docker",
        "WORKSPACE_IMAGE": os.getenv("SCENARIOS_WORKSPACE_IMAGE", WORKSPACE_IMAGE),
        "FUNCTION_IMAGE": os.getenv("SCENARIOS_FUNCTION_IMAGE", FUNCTION_IMAGE),
        # The images are tags we build locally, not digests.
        "WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES": "true",
        # A sandbox is a container; the backend it calls back to is on the host.
        "WORKSPACE_ADD_HOST_GATEWAY": "true",
        "WORKSPACE_HOST_ALIAS": "host.docker.internal",
        "WORKSPACE_CALLBACK_API_URL": f"http://host.docker.internal:{port}",
        "FUNCTION_RUNTIME_GATEWAY_URL": f"http://host.docker.internal:{port}",
    }


#: Settings a deployment may hold that would change *how the product behaves*,
#: as opposed to *what it can reach*. The stack decides these, always.
#:
#: The distinction is the whole basis for reading a developer's `.env` at all. A
#: key that lets Lemma talk to GitHub is worth inheriting — it is the point. A
#: switch that changes which code path runs is not: inherit it and the suite
#: passes or fails depending on whose machine it is running on, which is the one
#: property a test suite must never have.
#:
#: Every entry here was earned. `ENABLE_TELEGRAM_POLLING_MODE` is on in at least
#: one developer's config, and with it on Lemma registers no webhook — so every
#: surface scenario failed with "the surface never connected", on that machine
#: only. The live lane turns polling back on deliberately, because a real bot on
#: a runner with no public address has no other way to receive.
DECIDED_BY_THE_STACK = (
    # How surfaces receive. Scenarios deliver webhooks themselves.
    "ENABLE_TELEGRAM_POLLING_MODE",
    "ENABLE_TELEGRAM_MANAGER_POLLING_MODE",
    "ENABLE_SLACK_SOCKET_MODE",
    # Where sandboxes run. `WORKSPACE_PROVIDER` is pinned to docker below, and a
    # stray hosted-provider key would send function runs somewhere else.
    "AGENTBOX_API_KEY",
    "AGENTBOX_API_URL",
    "E2B_API_KEY",
    # Whether documents are converted. PS-DATA-041 is specifically about what a
    # deployment does when conversion is unavailable, so it has to be.
    "DOCUMENT_PROCESSOR",
    "KREUZBERG_URL",
    # Where the product thinks it lives. The stack claims a public HTTPS address
    # so surfaces can be created at all; a real one here breaks its own URLs.
    "APP_BASE_DOMAIN",
    "CLI_API_URL",
    "CLI_AUTH_FRONTEND_URL",
)


def _inheritable(deployment: dict[str, str]) -> dict[str, str]:
    """The deployment's settings, minus the ones the stack must decide itself."""
    return {
        name: value
        for name, value in deployment.items()
        if name not in DECIDED_BY_THE_STACK
    }


def _deployment_settings() -> dict[str, str]:
    """The operator's own configuration, for the lane that wants it.

    Off by default, and that default is not a precaution — it is the fast lane's
    whole value. A suite that reads whatever a developer happens to have
    configured gives different answers on different machines, and two scenarios
    proved it before this switch existed: "installing an OAuth connector with no
    credentials is refused" passed in CI and failed on a laptop whose `.env` had
    Slack credentials, and the product was right both times.

    The live lane wants the opposite, because its question is whether Lemma
    works against what this deployment is actually configured for. So it opts
    in, and gets the settings the backend itself reads — no parallel namespace
    of test-only credentials.

    Even opted in, `DECIDED_BY_THE_STACK` still applies, and every setting that
    decides where state lives is set explicitly afterwards and wins.
    `test_stack_never_inherits_real_infrastructure` fails the build otherwise.
    """
    if os.getenv("SCENARIOS_USE_DEPLOYMENT_ENV", "").lower() not in {"1", "true", "yes"}:
        return {}
    return _inheritable(load_deployment_env())


def _configured_or(name: str, fallback: str) -> str:
    """What the deployment set, or a placeholder that keeps the stack bootable."""
    settings = {**_deployment_settings(), **os.environ}
    return settings.get(name) or fallback


def _seed_connectors(python_bin: str, env: dict[str, str]) -> None:
    """Import the native connector catalogue.

    Without it the `connectors` table is empty, so installing Slack or Telegram
    answers "connector not found" — and a surface cannot be connected at all,
    because a surface binds to an account of an installed connector. Native apps
    only; the Composio half is skipped when no key is set.

    Best-effort: a stack that cannot seed the catalogue still serves every
    journey that does not touch connectors, and failing the whole boot for that
    would be worse than the scenarios that need it failing on their own terms.
    """
    # Native only by default: importing Composio's toolkits costs a network
    # round trip and minutes, which is the wrong trade for a lane that runs on
    # every push. The live lane sets `all`, because a catalogue missing half its
    # connectors is one of the things that lane exists to notice.
    catalogue = os.getenv("SCENARIOS_CONNECTOR_CATALOGUE", "native")
    result = subprocess.run(
        [python_bin, "scripts/import_connector_catalog.py"]
        + ([] if catalogue == "all" else ["--provider", "native"]),
        cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(
            "warning: connector catalogue not seeded; connector and surface "
            f"scenarios will fail.\n{(result.stderr or result.stdout)[-800:]}"
        )


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
        _seed_connectors(python_bin, env)

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
