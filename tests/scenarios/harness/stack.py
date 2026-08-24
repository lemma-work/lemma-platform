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

import base64
import json
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
from harness import egress as egress_proxy
from harness.egress import Egress

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "lemma-backend"

POSTGRES_IMAGE = "docker.io/pgvector/pgvector:0.8.3-pg18"
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

#: Two things a local stack cannot fake its way through, and an override for
#: each. An OAuth callback has to come back to an address the *browser* can
#: reach and the provider has registered, and a real Telegram refuses a webhook
#: whose host does not resolve — both were found the hard way. Set
#: `SCENARIOS_PUBLIC_API_URL` (with `SCENARIOS_PORT`, so the address is stable
#: enough to register once) to run a consent flow against a stack on this
#: machine, or to point a tunnel at one.
PUBLIC_URL_SETTING = "SCENARIOS_PUBLIC_API_URL"
PORT_SETTING = "SCENARIOS_PORT"


def public_api_url(port: int) -> str:
    return os.getenv(PUBLIC_URL_SETTING, "").strip() or PUBLIC_API_URL


#: Organizations whose slug starts with this are capped at zero monthly spend by
#: the stack's configuration below. PS-OPS-012 promises work over a limit is
#: refused, and until a deployment could state a limit at all there was nowhere
#: that promise could run. Scoped by slug prefix so the cap lands on the probe
#: organization a scenario creates and on nothing else -- every other
#: organization on the stack stays unlimited.
SPEND_CAP_PROBE_SLUG_PREFIX = "spend-cap-probe"

#: The domain this stack's email surfaces get their addresses under.
RESEND_INBOUND_DOMAIN = "scenarios.lemma.example"

#: The Svix signing secret the stack runs with.
#:
#: Built rather than written down. The verifier only needs valid base64 after
#: the `whsec_` prefix, and a literal of that shape is indistinguishable from a
#: real signing secret — to a reader, and to the secret scanner, which is right
#: to flag it. `.gitleaks.toml` allows exactly one such value and says why any
#: other high-entropy string stays a finding; the way to honour that is to not
#: produce one, rather than to widen the allowlist.
RESEND_WEBHOOK_SECRET = (
    "whsec_" + base64.b64encode(b"lemma-scenarios-resend-signing").decode()
)


def webhook_signing_secret() -> str:
    """The key a signed inbound email must be signed with, wherever this points.

    The constant above is what a stack the suite boots is configured with, and
    asserting it against a deployment fails on a correct product for exactly
    the reason `inbound_email_domain` gives one line down: the deployment has
    its own, and ours describes a different machine. Signed with the
    placeholder, a real deployment answers 401 `SURFACE_WEBHOOK_AUTH_FAILED` —
    a scenario reporting the product broken when the product was right.
    """
    return _configured_or("RESEND_WEBHOOK_SECRET", RESEND_WEBHOOK_SECRET)


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

    #: What Lemma said to the outside world, if anything is listening. See
    #: `harness/egress.py`; `mode == "off"` when nothing is.
    egress: "Egress | None" = None

    #: Did this process start it? A stack the suite booted is empty and
    #: disposable, so the run builds the standing tenant in it as a matter of
    #: course. A deployment is somebody's, and a run must never quietly
    #: register accounts there — it says the tenant is missing and stops.
    ours: bool = True

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


def _refuse_a_port_in_use(port: int) -> None:
    """Fail rather than let a pinned port attach the suite to somebody else.

    `_free_port` cannot collide, but `SCENARIOS_PORT` names one, and a stack
    that failed to bind still goes on to wait for health — which whatever
    already holds the port happily answers. The run then tests that process:
    a stale build, with none of this run's settings. It cost an afternoon
    once, and every symptom pointed at the settings rather than the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return
    raise RuntimeError(
        f"{PORT_SETTING} is {port}, but something already listens there. "
        "Stop it first: a stack that cannot bind would still find that "
        "process healthy and quietly run every scenario against it."
    )


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


#: Ask for infrastructure that stands between runs. Off by default, because a
#: suite that leaves containers and a database behind on every developer's
#: machine is a rude default — and because a fresh database is the right answer
#: for CI, where nothing has consented to anything.
#:
#: On, it is what makes the standing tenant actually stand. GitHub, Slack and
#: Gmail accounts exist only after a person consented in a browser, and the
#: product has no way to store one without that (correctly). Throwing the
#: database away each run therefore throws away the one thing the suite cannot
#: recreate for itself — so re-running anything that touches a real connector
#: meant asking a person to click through OAuth again, every time.
STANDING_SETTING = "SCENARIOS_STANDING_STACK"

#: Named, so they can be found again. `_docker_run` deliberately names nothing.
STANDING_NETWORK = "lemma-scenarios"
STANDING_POSTGRES = "lemma-scenarios-postgres"
STANDING_REDIS = "lemma-scenarios-redis"
STANDING_SUPERTOKENS = "lemma-scenarios-supertokens"

#: Supertokens keeps its own tables. A database of its own rather than sharing
#: `test`, so `alembic downgrade` and the sweep can never reach them: losing
#: those is losing every password, with the application database left intact
#: and pointing at users who can no longer sign in.
STANDING_SUPERTOKENS_DB = "supertokens"


def standing_wanted() -> bool:
    """Whether this run wants infrastructure that outlives it."""
    return os.getenv(STANDING_SETTING, "").lower() in {"1", "true", "yes"}


def _network_exists() -> None:
    subprocess.run(
        ["docker", "network", "create", STANDING_NETWORK],
        capture_output=True,
        text=True,
    )


def _standing_container(
    name: str,
    image: str,
    internal_port: int,
    env: dict[str, str] | None = None,
    volume: str | None = None,
) -> str:
    """The named container: reused if it is there, started if it is stopped.

    On a user-defined network so the containers can reach each other by name —
    which Supertokens needs, since its storage is a Postgres it has to dial
    itself. Published on 127.0.0.1 as well, for the API process on the host.
    """
    existing = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if existing:
        running = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"name=^{name}$"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not running:
            subprocess.run(["docker", "start", name], check=True, capture_output=True)
        return existing

    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--label",
        CONTAINER_LABEL,
        "--network",
        STANDING_NETWORK,
        "-p",
        f"127.0.0.1::{internal_port}",
    ]
    if volume:
        # A *named* volume, so `docker rm` without -v keeps the data and a
        # container rebuilt on a new image still finds it.
        command += ["-v", f"{name}-data:{volume}"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    command.append(image)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise StackError(
            f"could not start {name}: {(result.stderr or result.stdout).strip()[:500]}"
        )
    return result.stdout.strip()


def _database_exists(postgres: str, name: str) -> None:
    """Create a database if it is not there. Idempotent, by inspection."""
    listed = subprocess.run(
        [
            "docker",
            "exec",
            postgres,
            "psql",
            "-U",
            POSTGRES_USER,
            "-d",
            POSTGRES_DB,
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{name}'",
        ],
        capture_output=True,
        text=True,
    )
    if listed.stdout.strip() == "1":
        return
    subprocess.run(
        ["docker", "exec", postgres, "createdb", "-U", POSTGRES_USER, name],
        check=True,
        capture_output=True,
        text=True,
    )


def _docker_run(image: str, internal_port: int, env: dict[str, str] | None = None) -> str:
    command = [
        "docker",
        "run",
        "-d",
        "--label",
        CONTAINER_LABEL,
        "-p",
        f"127.0.0.1::{internal_port}",
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
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])


def _remove(container_id: str) -> None:
    # -v also removes the container's anonymous data volume (postgres/redis/
    # supertokens all declare VOLUME in their image) — without it every
    # teardown, even a clean one, leaked one volume forever. Found via three
    # random-named containers (docker's default naming for a container run
    # without --name) sitting exited on a dev machine for 21+ hours.
    subprocess.run(
        ["docker", "rm", "-f", "-v", container_id], check=False, capture_output=True
    )


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
            # Not up yet. Both mean "nothing answered": connection refused
            # while the port is still closed, and a read timeout while the
            # process is binding. Neither is a failure until the deadline.
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


def _environment(
    *,
    port: int,
    database_url: str,
    redis_url: str,
    supertokens_url: str,
    egress: Egress | None = None,
) -> dict[str, str]:
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
        "API_URL": public_api_url(port),
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
        # Export and import are capped at five a day *per user*, which is a
        # sensible guard on a real deployment and an impossible one for a suite
        # driven by a standing cast: the packaging journey alone spends more
        # than that in a single run, and the cap is per UTC day, so the run
        # after it would have none left. Off here, and reported by
        # /health/capabilities so a deployment lane can say so rather than fail
        # halfway through.
        "POD_BUNDLE_DAILY_EXPORT_LIMIT": "0",
        "POD_BUNDLE_DAILY_IMPORT_LIMIT": "0",
        # A deployment that states its own spend limits, so the refusal path in
        # PS-OPS-012 has somewhere to run. Only the probe organization is
        # capped; see SPEND_CAP_PROBE_SLUG_PREFIX.
        "USAGE_ORG_LIMIT_OVERRIDES_JSON": json.dumps(
            [{"slug_prefix": SPEND_CAP_PROBE_SLUG_PREFIX, "monthly_limit_usd": 0}]
        ),
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
        # Caches, pinned short.
        #
        # Every one of these is Redis-backed with a long production TTL — the
        # user cache is half an hour, the authorization role snapshot five
        # minutes — and every one is invalidated on write. A test that creates
        # something and immediately reads it back is racing that invalidation,
        # and on a two-core runner it loses: five journey shards were failing
        # with 403 on creating an agent and "carol is not a member of pod"
        # moments after she was added.
        #
        # Short rather than zero, deliberately. At zero the caching path stops
        # existing and the suite would no longer run the code a deployment runs.
        # At one second it is still exercised, staleness closes inside the time
        # any scenario takes to make its next call, and `PS-POD-011` — which
        # polls for a role change on purpose — keeps meaning what it says.
        "USER_CACHE_TTL_SECONDS": "1",
        "AUTHORIZATION_ROLE_CACHE_TTL_SECONDS": "1",
        "ORGANIZATION_HOME_CACHE_TTL_SECONDS": "1",
        "AUTH_STATE_CACHE_TTL_SECONDS": "1",
        # Not the function session token cache: its setting refuses anything
        # below 30, and it caches a token rather than a read a scenario races.
        # Off, so surface scenarios can deliver webhooks and see them arrive.
        # The live lane sets it: a real bot on a runner with no public address
        # has no other way to receive, and there is no webhook to deliver.
        "ENABLE_TELEGRAM_POLLING_MODE": os.getenv("SCENARIOS_TELEGRAM_POLLING", "false"),
        # Slack's counterpart. Socket mode is a WebSocket out to Slack, so a
        # workspace can reach a stack with no public address — the only way a
        # Slack surface receives anything on a laptop or a runner.
        "ENABLE_SLACK_SOCKET_MODE": os.getenv("SCENARIOS_SLACK_SOCKET", "false"),
        # And email's. Resend's inbound webhook is push-only; polling lists the
        # account's received mail instead, which is what lets a real round trip
        # — send from a mailbox, agent answers — run with nothing published.
        "ENABLE_RESEND_POLLING_MODE": os.getenv("SCENARIOS_RESEND_POLLING", "false"),
        # Email surfaces. A Resend inbound webhook is Svix-signed, so without a
        # secret the endpoint answers 503 and no email scenario can run at all;
        # this is a well-formed throwaway, and scenarios sign with it exactly as
        # Resend would. It stays a throwaway even on the real-email lane: no
        # webhook from Resend ever arrives here, and the suite has to be able to
        # sign the ones it delivers itself.
        "RESEND_WEBHOOK_SECRET": RESEND_WEBHOOK_SECRET,
        # The domain is what gives each surface its own address, and the key is
        # what makes a send real. Both come from the deployment on the
        # real-email lane, and both have to move together: a real key against
        # the placeholder domain sends from a domain Resend has not verified.
        #
        # Opt-in rather than "use whatever is configured", because the default
        # lane replies to senders that scenarios invented. Against a real key
        # every one of those is a hard bounce at a reserved domain, charged to
        # the sending reputation of an account the product itself uses.
        **_real_email_settings(),
        # `CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS` is deliberately absent, so
        # this stack runs the same SSRF posture as production. It used to be on
        # because the stand-ins bound loopback and a connector had to reach
        # them; the proxy answers for real hostnames now, so nothing does.
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
        # Where those credentials are actually valid. Passed through for the
        # same reason as the key, and it was the one of the four that was not:
        # a deployment serving its model from anywhere other than OpenAI — an
        # OpenAI-compatible gateway, a self-hosted server — had the key and the
        # model name carried over while the base URL silently fell back to
        # api.openai.com. Every real-model scenario then failed on a provider
        # error that looked like the model being unreliable.
        "LEMMA_OPENAI_BASE_URL": _configured_or(
            "LEMMA_OPENAI_BASE_URL", "https://api.openai.com/v1"
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
        # Last, so it wins. A developer with their own HTTPS_PROXY set would
        # otherwise send the product's traffic somewhere this run cannot read,
        # and every assertion about what Lemma sent would come back empty with
        # nothing to say why.
        **(egress.environment() if egress is not None else {}),
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
    # The fourth receiver toggle, missing from this list until now: a
    # developer whose .env turned Resend polling on would have had the suite
    # receiving mail differently from everybody else's, and passing or failing
    # on that difference.
    "ENABLE_RESEND_POLLING_MODE",
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


REAL_EMAIL_SETTING = "SCENARIOS_REAL_EMAIL"


def inbound_email_domain() -> str:
    """The domain a surface's address will be on, wherever this run points.

    A stack the suite boots uses the placeholder below. A deployment has its
    own, and asserting the placeholder against it fails on a correct product —
    the address really is on that deployment's domain, just not on ours.
    """
    return _configured_or("RESEND_INBOUND_DOMAIN", RESEND_INBOUND_DOMAIN)


def _real_email_settings() -> dict[str, str]:
    """Placeholder Resend credentials, or the deployment's real ones.

    Real ones only when asked for by name. See the call site for why this is
    opt-in and why the key and the domain are read as a pair.
    """
    if os.getenv(REAL_EMAIL_SETTING, "").lower() not in ("1", "true", "yes"):
        return {
            "RESEND_INBOUND_DOMAIN": RESEND_INBOUND_DOMAIN,
            "RESEND_API_KEY": "re_scenarios_not_a_real_key",
        }
    real = {
        name: _configured_or(name, "")
        for name in ("RESEND_API_KEY", "RESEND_INBOUND_DOMAIN")
    }
    missing = sorted(name for name, value in real.items() if not value)
    if missing:
        raise RuntimeError(
            f"{REAL_EMAIL_SETTING} is set, but {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not configured. "
            "Real email needs a Resend key and a domain verified in that "
            "same Resend account; without both, every send fails at Resend."
            + (
                " Both are read from the deployment's own configuration, which "
                "SCENARIOS_USE_DEPLOYMENT_ENV=1 is what opens."
                if not os.getenv("SCENARIOS_USE_DEPLOYMENT_ENV")
                else ""
            )
        )
    return real


def _configured_or(name: str, fallback: str) -> str:
    """What the deployment set, or a placeholder that keeps the stack bootable."""
    settings = {**_deployment_settings(), **os.environ}
    return settings.get(name) or fallback


WORKERS_SETTING = "SCENARIOS_WORKERS"

#: Receivers that must not be run twice. A polling receiver is a single
#: consumer by construction — Telegram answers a second `getUpdates` for the
#: same bot with 409 Conflict, and the two pollers then take turns losing.
_SINGLE_CONSUMER_RECEIVERS = (
    "ENABLE_TELEGRAM_POLLING_MODE",
    "ENABLE_TELEGRAM_MANAGER_POLLING_MODE",
    "ENABLE_SLACK_SOCKET_MODE",
    "ENABLE_RESEND_POLLING_MODE",
)


def _how_many_workers(env: dict[str, str]) -> int:
    """How many worker processes to run. One unless asked for more.

    The product expects replicas — `schedule_poller` says so in as many words:
    "Every replica runs this. Nothing elects a leader; the claim decides who
    fires." The suite ran exactly one, which is fine per journey and is what CI
    does, and is the wrong shape for a local run of every journey at once: 380
    scenarios queue their agent runs through a single event loop, and the first
    thing to give is a scenario waiting on a reply that is merely behind a
    queue. More workers is the honest fix, because it is the deployment shape.

    Refused where a polling receiver is on, because those are single-consumer.
    """
    asked = os.getenv(WORKERS_SETTING, "").strip()
    if not asked:
        return 1
    try:
        many = int(asked)
    except ValueError:
        raise StackError(f"{WORKERS_SETTING} must be a number, got {asked!r}") from None
    if many < 1:
        raise StackError(f"{WORKERS_SETTING} must be at least 1, got {many}")
    if many == 1:
        return 1
    polling = [
        name
        for name in _SINGLE_CONSUMER_RECEIVERS
        if str(env.get(name, "")).lower() in {"1", "true", "yes"}
    ]
    if polling:
        raise StackError(
            f"{WORKERS_SETTING}={many} with {', '.join(polling)} on. Those "
            f"receivers are single-consumer: a second poller asking Telegram "
            f"for the same bot's updates is answered 409 Conflict, and the two "
            f"take turns losing messages. Run one worker, or turn them off."
        )
    return many


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
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(
            "warning: connector catalogue not seeded; connector and surface "
            f"scenarios will fail.\n{(result.stderr or result.stdout)[-800:]}"
        )


def _migrate(python_bin: str, env: dict[str, str]) -> None:
    result = subprocess.run(
        [python_bin, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
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
    scratch = Path(tempfile.gettempdir()) / f"lemma-scenarios-egress-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    # Before the product, because the product is booted with its address; after
    # everything on the way down, because it is recording their traffic.
    egress = egress_proxy.start(
        egress_proxy.wanted_mode(),
        cassette=os.getenv("SCENARIOS_CASSETTE", "all"),
        scratch=scratch,
    )

    standing = standing_wanted()
    if standing:
        _network_exists()

    try:
        credentials = {
            "POSTGRES_USER": POSTGRES_USER,
            "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
            "POSTGRES_DB": POSTGRES_DB,
        }
        if standing:
            postgres = _standing_container(
                STANDING_POSTGRES,
                POSTGRES_IMAGE,
                5432,
                credentials,
                # The volume this image actually declares. PGDATA lives at
                # /var/lib/postgresql/18/docker *inside* it — mounting the
                # older /var/lib/postgresql/data persists an empty directory
                # and loses everything, silently.
                volume="/var/lib/postgresql",
            )
        else:
            postgres = _docker_run(POSTGRES_IMAGE, 5432, credentials)
            containers.append(postgres)
        postgres_port = _mapped_port(postgres, 5432)
        _wait_postgres("127.0.0.1", postgres_port)
        subprocess.run(
            [
                "docker",
                "exec",
                postgres,
                "psql",
                "-U",
                POSTGRES_USER,
                "-d",
                POSTGRES_DB,
                "-c",
                "CREATE EXTENSION IF NOT EXISTS vector",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        if standing:
            # Redis holds caches and streams, not the tenant. It stands only so
            # the three move together; nothing here would be lost by dropping it.
            redis = _standing_container(STANDING_REDIS, REDIS_IMAGE, 6379)
        else:
            redis = _docker_run(REDIS_IMAGE, 6379)
            containers.append(redis)
        redis_port = _mapped_port(redis, 6379)
        _wait_tcp("127.0.0.1", redis_port)

        if standing:
            # Given storage, at last. Without POSTGRESQL_CONNECTION_URI this
            # image keeps everything in memory, so a persisted application
            # database would survive with every password gone — users intact
            # and nobody able to sign in, which is worse than not persisting.
            _database_exists(postgres, STANDING_SUPERTOKENS_DB)
            supertokens = _standing_container(
                STANDING_SUPERTOKENS,
                SUPERTOKENS_IMAGE,
                3567,
                {
                    "POSTGRESQL_CONNECTION_URI": (
                        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
                        f"@{STANDING_POSTGRES}:5432/{STANDING_SUPERTOKENS_DB}"
                    )
                },
            )
        else:
            supertokens = _docker_run(SUPERTOKENS_IMAGE, 3567)
            containers.append(supertokens)
        supertokens_port = _mapped_port(supertokens, 3567)
        _wait_http(f"http://127.0.0.1:{supertokens_port}/hello")

        pinned = os.getenv(PORT_SETTING, "")
        port = int(pinned) if pinned else _free_port()
        if pinned:
            _refuse_a_port_in_use(port)
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
            egress=egress,
        )

        python_bin = _backend_python()
        _migrate(python_bin, env)
        _seed_connectors(python_bin, env)

        # No scheduler sidecar. APScheduler and `app/scheduler.py` were deleted
        # in #362; time schedules are driven from the worker now. Booting one
        # here is what `lemma-cli/tests/e2e/conftest.py` still does, which is
        # why that suite fails before its first assertion — see DEV-OPS-001.
        processes.append(
            subprocess.Popen(
                [
                    python_bin,
                    "-m",
                    "uvicorn",
                    "app.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=str(BACKEND_ROOT),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        )
        base_url = f"http://127.0.0.1:{port}"
        _wait_http(f"{base_url}/health", timeout=120)

        # The worker. Agent runs, workflow resumes, scheduled fires and document
        # processing are all queued rather than done in the request, so without
        # this the API accepts the work and nothing ever picks it up — which
        # looks exactly like a product bug from a scenario's point of view.
        for _ in range(_how_many_workers(env)):
            processes.append(
                subprocess.Popen(
                    [python_bin, "-m", "app.worker"],
                    cwd=str(BACKEND_ROOT),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )

        yield Stack(
            base_url=base_url,
            redis_url=redis_url,
            database_url=database_url,
            log_path=str(log_path),
            egress=egress,
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
        # Last: mitmproxy only flushes its recording when it exits, so a run
        # that tore this down first would lose the final calls it made — and a
        # recording missing its own tail replays as a mystery.
        egress_proxy.stop(egress)
        log.close()
