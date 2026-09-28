"""A disposable Lemma built from released images, for `--stack compose`.

The local stack (`harness/stack.py`) runs this checkout's backend as processes
on this machine: fast, and exactly the code under review. What it cannot say is
anything about the *images* a deployment runs. This is the other half: the
self-hosting Compose file in `deploy/compose`, brought up from a release — or
from any images you name — with its sign-up gates off, driven over HTTP, and
removed with its volumes afterwards.

That is what lets sign-up scenarios run on every release. A deployment keeps its
gates on, so they skip there; run them here instead, against the same images,
and run everything else against the deployment. See `make scenarios-split`.

Differences from `deploy/compose` as a person installs it, each on purpose:

- **A copy, in a temporary directory.** `bootstrap.sh` writes `.env` next to
  itself, and a test run has no business writing into the checkout.
- **No Caddy, no frontend.** The suite speaks HTTP to the API, published on a
  loopback port; TLS and the web app are not what these scenarios prove, and
  Caddy's fixed host ports would collide with anything else on the machine.
- **`ENVIRONMENT=testing` and every sign-up gate off,** so the target reports
  its posture on `/health/capabilities` and `OPEN_SIGNUP` resolves to yes.
- **Sandbox images are not pulled** unless `SCENARIOS_COMPOSE_SANDBOX=1` —
  gigabytes a sign-up scenario never touches.
- **Its own project and network names,** so it cannot join a real install's.

Images: `SCENARIOS_COMPOSE_VERSION` (a release, default the latest) or
`SCENARIOS_COMPOSE_MANIFEST` (a `lemma-local.json`), and any single one of them
replaced by `SCENARIOS_BACKEND_IMAGE` / `SCENARIOS_FRONTEND_IMAGE` or by a
`pytest_scenarios_configure_stack` hook setting `spec.images`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from harness.stack import (
    ROOT,
    Stack,
    StackError,
    StackSpec,
    _free_port,
    _wait_http,
    require_docker,
)

COMPOSE_SOURCE = ROOT / "deploy" / "compose"

VERSION_SETTING = "SCENARIOS_COMPOSE_VERSION"
MANIFEST_SETTING = "SCENARIOS_COMPOSE_MANIFEST"
SANDBOX_SETTING = "SCENARIOS_COMPOSE_SANDBOX"

#: `.env` keys a single environment variable may replace, for testing one image
#: — the one a deployment is about to run — against an otherwise released stack.
IMAGE_OVERRIDES = {
    "LEMMA_BACKEND_IMAGE": "SCENARIOS_BACKEND_IMAGE",
    "LEMMA_FRONTEND_IMAGE": "SCENARIOS_FRONTEND_IMAGE",
}

#: What makes this stack disposable rather than a deployment. Mirrors the
#: relaxations `harness/stack.py` gives the local stack, minus the ones that
#: only make sense for processes on this machine.
TESTING_SETTINGS = {
    "ENVIRONMENT": "testing",
    "AUTH_ABUSE_PROTECTION_ENABLED": "false",
    "AUTH_ALTCHA_ENABLED": "false",
    "AUTH_EMAIL_VERIFICATION_REQUIRED": "false",
    "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED": "false",
    "AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED": "false",
    "USER_CACHE_TTL_SECONDS": "1",
    "AUTHORIZATION_ROLE_CACHE_TTL_SECONDS": "1",
    "ORGANIZATION_HOME_CACHE_TTL_SECONDS": "1",
    "AUTH_STATE_CACHE_TTL_SECONDS": "1",
    "POD_BUNDLE_DAILY_EXPORT_LIMIT": "0",
    "POD_BUNDLE_DAILY_IMPORT_LIMIT": "0",
    "SESSION_COOKIE_SECURE": "false",
    "SESSION_COOKIE_DOMAIN": "",
}

#: The backend services, which all read the same settings.
BACKEND_SERVICES = ("migrate", "api", "worker")


def start_compose_stack(
    configure: Callable[[StackSpec], None] | None = None,
) -> Iterator[Stack]:
    """Bring up `deploy/compose` from images, yield it, and take it away."""
    require_docker()
    project = f"lemma-scenarios-compose-{os.getpid()}"
    log_path = Path(tempfile.gettempdir()) / f"lemma-scenarios-{project}.log"
    work = Path(tempfile.mkdtemp(prefix="lemma-scenarios-compose-"))
    shutil.copytree(COMPOSE_SOURCE, work, dirs_exist_ok=True)
    (work / ".env").unlink(missing_ok=True)

    port = _free_port()
    spec = StackSpec(
        kind="compose",
        port=port,
        base_url=f"http://127.0.0.1:{port}",
        env=dict(TESTING_SETTINGS),
    )
    for key, setting in IMAGE_OVERRIDES.items():
        if os.getenv(setting):
            spec.images[key] = os.environ[setting]
    if configure is not None:
        configure(spec)

    started = False
    try:
        _bootstrap(work)
        _pin_images(work / ".env", spec.images)
        (work / "scenarios.override.yml").write_text(
            _override(project, port, spec.env, _commands(spec)), encoding="utf-8"
        )
        compose = _compose_in(work, project)
        started = True
        services = [*BACKEND_SERVICES]
        up = compose("up", "-d", "--wait", "--wait-timeout", "900", *services)
        if up.returncode != 0:
            raise StackError(
                f"the Compose stack did not come up:\n"
                f"{(up.stderr or up.stdout)[-2000:]}\n\n{_logs(compose)}"
            )
        _wait_http(f"{spec.base_url}/health/ready", timeout=300)
        yield Stack(
            base_url=spec.base_url,
            redis_url="",
            database_url="",
            log_path=str(log_path),
            egress=None,
            ours=True,
            extras=spec.extras,
        )
    finally:
        if started:
            compose = _compose_in(work, project)
            log_path.write_text(_logs(compose), encoding="utf-8")
            compose("down", "--volumes", "--remove-orphans")
        shutil.rmtree(work, ignore_errors=True)


def _bootstrap(work: Path) -> None:
    """Write `.env` the way a person installing Lemma would."""
    arguments = ["./bootstrap.sh", "--domain", "127.0.0.1.sslip.io"]
    if os.getenv(MANIFEST_SETTING):
        arguments += ["--manifest", str(Path(os.environ[MANIFEST_SETTING]).resolve())]
    elif os.getenv(VERSION_SETTING):
        arguments += ["--version", os.environ[VERSION_SETTING]]
    result = subprocess.run(
        arguments, cwd=work, capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        raise StackError(
            f"deploy/compose/bootstrap.sh failed:\n{(result.stderr or result.stdout)[-2000:]}"
        )


def _pin_images(env_file: Path, images: dict[str, str]) -> None:
    """Replace named image references in the bootstrapped `.env`."""
    if not images:
        return
    lines = env_file.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if key in images:
            lines[index] = f"{key}={images[key]}"
            seen.add(key)
    lines += [f"{key}={value}" for key, value in images.items() if key not in seen]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _commands(spec: StackSpec) -> dict[str, list[str]]:
    """What each backend service runs, when the spec says something else.

    The same fields the local stack reads — `app`, `worker`, `migrations` — so
    one `pytest_scenarios_configure_stack` hook describes an application for
    both kinds of stack. Left alone, the images' own commands in
    deploy/compose run, which are the open-source backend's.
    """
    default = StackSpec(kind="compose", port=0, base_url="")
    commands: dict[str, list[str]] = {}
    if spec.app != default.app:
        commands["api"] = ["uvicorn", spec.app, "--host", "0.0.0.0", "--port", "8000"]
    if spec.worker != default.worker:
        commands["worker"] = ["python", *spec.worker]
    if spec.migrations != default.migrations:
        steps = " && ".join(
            "python " + " ".join(shlex.quote(part) for part in step)
            for step in spec.migrations
        )
        commands["migrate"] = ["/bin/sh", "-euc", steps]
    return commands


def _override(
    project: str,
    port: int,
    settings: dict[str, str],
    commands: dict[str, list[str]] | None = None,
) -> str:
    """The Compose override that makes an install a disposable test target."""
    commands = commands or {}
    services = []
    for service in BACKEND_SERVICES:
        # Where this API answers, since there is no Caddy in front of it to
        # answer at the bootstrapped https address.
        values = {**settings, "API_URL": f"http://127.0.0.1:{port}"}
        block = f"  {service}:\n"
        if service in commands:
            block += f"    command: {json.dumps(commands[service])}\n"
        if service == "api":
            block += f'    ports: ["127.0.0.1:{port}:8000"]\n'
        block += "    environment:\n" + "".join(
            f"      {key}: {_quoted(value)}\n" for key, value in sorted(values.items())
        )
        services.append(block)
    if os.getenv(SANDBOX_SETTING, "").lower() not in {"1", "true", "yes"}:
        services.append(
            "  sandbox-images:\n"
            '    entrypoint: ["/bin/sh", "-c"]\n'
            '    command: ["echo sandbox images not pulled for this run"]\n'
        )
    return (
        "services:\n"
        + "".join(services)
        + "networks:\n"
        + f"  data: {{name: {project}-data}}\n"
        + f"  sandbox: {{name: {project}-sandbox}}\n"
        + f"  edge: {{name: {project}-edge}}\n"
    )


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _compose_in(work: Path, project: str) -> Callable[..., subprocess.CompletedProcess]:
    def compose(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                "docker-compose.yml",
                "-f",
                "scenarios.override.yml",
                *arguments,
            ],
            cwd=work,
            capture_output=True,
            text=True,
        )

    return compose


def _logs(compose: Callable[..., subprocess.CompletedProcess]) -> str:
    listed = compose("ps", "-a")
    logs = compose("logs", "--no-color", "--tail", "200")
    return f"{listed.stdout}\n{logs.stdout}{logs.stderr}"
