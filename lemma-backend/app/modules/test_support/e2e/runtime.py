"""Shared E2E runtime fixtures for worker, workspace, and scheduler tests."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import socket
import subprocess
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
import httpx
import uvicorn

from app.core.config import settings
from app.modules.schedule.config import schedule_settings
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    skip_unless_system_lemma,
    system_lemma_api_key,
    system_lemma_env_overlay,
)


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_env_file_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def _e2b_environment(key: str) -> str | None:
    """Resolve protected E2B test configuration without mutating the process env."""

    configured = os.getenv(key)
    if configured:
        return configured
    repo_root = Path(__file__).resolve().parents[5]
    backend_value = _read_env_file_value(repo_root / "lemma-backend" / ".env", key)
    if backend_value:
        return backend_value
    return None


_NGROK_URL = re.compile(r"https://[a-z0-9-]+\.(?:ngrok-free\.app|ngrok\.app)")
_CLOUDFLARE_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


async def _terminate_subprocess(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


@asynccontextmanager
async def _temporary_workspace_tunnel(
    backend_url: str,
    *,
    wait_for_backend: bool = True,
) -> AsyncIterator[str]:
    """Expose the test backend to a remote E2B workspace for CLI callbacks.

    `wait_for_backend` is False for the session-scoped tunnel, which is
    opened before any test has bound the port beneath it. The tunnel itself
    does not need an upstream to exist -- it forwards whatever is listening
    when a request arrives -- so probing here would only assert an ordering
    that is deliberately the other way round.
    """

    configured = os.getenv("WORKSPACE_E2E_PUBLIC_URL")
    if configured:
        yield configured.rstrip("/")
        return

    tunnel = os.getenv("WORKSPACE_E2E_TUNNEL", "ngrok").strip().lower()
    if tunnel == "ngrok":
        command = (
            "ngrok",
            "http",
            backend_url,
            "--log",
            "stdout",
            "--log-format",
            "json",
            "--log-level",
            "info",
        )
        url_pattern = _NGROK_URL
    elif tunnel == "cloudflared":
        command = (
            "cloudflared",
            "tunnel",
            "--url",
            backend_url,
            "--no-autoupdate",
        )
        url_pattern = _CLOUDFLARE_URL
    else:
        raise RuntimeError("WORKSPACE_E2E_TUNNEL must be ngrok or cloudflared")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{tunnel} is required for E2B workspace E2E") from exc

    assert process.stdout is not None
    recent: list[str] = []

    async def consume_output(published: asyncio.Future[str]) -> None:
        while line := await process.stdout.readline():
            text = line.decode(errors="replace")
            recent.append(text)
            if len(recent) > 100:
                del recent[:-100]
            if not published.done() and (match := url_pattern.search(text)):
                published.set_result(match.group(0))
        if not published.done():
            published.set_exception(
                RuntimeError(
                    f"{tunnel} exited before publishing a tunnel: "
                    + "".join(recent[-20:])
                )
            )

    published = asyncio.get_running_loop().create_future()
    output_task = asyncio.create_task(consume_output(published))
    try:
        public_url = await asyncio.wait_for(asyncio.shield(published), timeout=45)
        if not wait_for_backend:
            yield public_url
            return
        last_health_result = "no response"
        # A freshly published tunnel hostname is not immediately resolvable,
        # and the quick-tunnel service is occasionally slow to route the first
        # request. Waiting a minute is far cheaper than a spurious failure in a
        # suite that takes minutes to reach this point.
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(60):
                if process.returncode is not None:
                    raise RuntimeError(
                        f"{tunnel} exited after publishing its URL: "
                        + "".join(recent[-20:])
                    )
                try:
                    response = await client.get(f"{public_url}/health")
                    last_health_result = (
                        f"HTTP {response.status_code}: {response.text[:500]}"
                    )
                    if response.status_code == 200:
                        break
                except httpx.HTTPError as exc:
                    last_health_result = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(
                    f"{tunnel} tunnel never reached backend health; "
                    f"last result: {last_health_result}"
                )
        yield public_url
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(output_task, return_exceptions=True)


# Source paths used by the canonical workspace and function images.
_AGENTBOX_BUILD_INPUTS = (
    "agentbox",
    "lemma-python",
    "lemma-pod-bundle",
    "lemma-cli",
    "lemma-typescript",
    "lemma-skills",
)


def _agentbox_image_fingerprint(repo_root: Path) -> str | None:
    """Short content hash of the agentbox runtime image's build inputs.

    Combines the committed git tree/blob hashes of the relevant paths with the
    current uncommitted diff and any untracked files, so the fingerprint changes
    exactly when the image would build differently — committed or not. Returns
    None when git can't be used (then the caller falls back to always building).
    """
    paths = list(_AGENTBOX_BUILD_INPUTS)
    try:
        rev = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", *[f"HEAD:{p}" for p in paths]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        diff = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "HEAD", "--", *paths],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        untracked = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                *paths,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError, FileNotFoundError:
        return None

    digest = hashlib.sha256()
    digest.update(rev.encode())
    digest.update(diff.encode())
    digest.update(untracked.encode())
    # `git diff` doesn't include untracked file contents — fold them in too.
    for rel in untracked.splitlines():
        candidate = repo_root / rel.strip()
        if candidate.is_file():
            with contextlib.suppress(OSError):
                digest.update(candidate.read_bytes())
    return digest.hexdigest()[:12]


@pytest.fixture(scope="module")
def workspace_image(e2e_settings) -> Generator[str, None, None]:
    """Ensure the docker workspace runtime image exists locally.

    Uses a content-addressed tag (``agentbox-runtime:e2e-<fingerprint>``) so the
    image is reused when agentbox + the bundled SDKs are unchanged, and rebuilt
    automatically when they change — no per-run rebuild (the build context is the
    whole monorepo, which is slow to transfer) and no stale pinned image. Set
    WORKSPACE_E2E_IMAGE to pin an explicit image (e.g. in CI).
    """
    repo_root = Path(__file__).resolve().parents[5]
    configured_image = os.getenv("WORKSPACE_E2E_IMAGE")
    if configured_image:
        image = configured_image
    else:
        fingerprint = _agentbox_image_fingerprint(repo_root)
        image = (
            f"agentbox-workspace:e2e-{fingerprint}"
            if fingerprint
            else "agentbox-workspace:e2e"
        )

    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    image_present = inspect.returncode == 0
    # Fall back to always-build only when we couldn't fingerprint (floating tag).
    should_build = not image_present or (
        not configured_image and image == "agentbox-workspace:e2e"
    )

    if should_build:
        dockerfile = repo_root / "lemma-backend" / "sandbox-images" / "Dockerfile.workspace"
        build = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(dockerfile),
                "-t",
                image,
                str(repo_root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            pytest.fail(
                "Workspace e2e failed to build workspace runtime image "
                f"'{image}'.\nSTDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
            )

    yield image


@pytest.fixture(scope="module")
def function_image(e2e_settings) -> Generator[str, None, None]:
    """Build the slim stateless function runner image used by AgentBox.

    Function artifacts currently declare the x86_64 runtime ABI, including for
    native wheels. Build the E2E runtime for that exact platform even on arm64
    developer machines; a native arm image would accept the profile but could
    not load the artifact's compiled extensions.
    """

    del e2e_settings
    repo_root = Path(__file__).resolve().parents[5]
    configured_image = os.getenv("FUNCTION_E2E_IMAGE")
    platform = os.getenv("FUNCTION_E2E_PLATFORM", "linux/amd64")
    if configured_image:
        image = configured_image
    else:
        fingerprint = _agentbox_image_fingerprint(repo_root)
        platform_tag = platform.rsplit("/", 1)[-1].replace("_", "-")
        image = (
            f"agentbox-function:e2e-{platform_tag}-{fingerprint}"
            if fingerprint
            else f"agentbox-function:e2e-{platform_tag}"
        )
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspect.returncode != 0:
        build = subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                platform,
                "-f",
                str(repo_root / "lemma-backend" / "sandbox-images" / "Dockerfile.function"),
                "-t",
                image,
                str(repo_root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            pytest.fail(
                "Function e2e image build failed "
                f"for '{image}'.\nSTDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
            )
    yield image


@pytest_asyncio.fixture(scope="function")
async def backend_server(test_app) -> AsyncGenerator[dict[str, str], None]:
    """Run a real backend HTTP server for Docker workspace callbacks."""

    # The production worker used by queued-function E2E is session-scoped and
    # captures its explicitly configured callback URL at startup. Rebind the
    # function-scoped backend to that stable port instead of silently relying on
    # localhost/container hostname rewriting.
    port = int(os.getenv("WORKSPACE_E2E_BACKEND_PORT") or _available_port())
    config = uvicorn.Config(
        app=test_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="websockets-sansio",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                exc = server_task.exception()
                raise RuntimeError("Backend server exited before startup") from exc
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Timed out starting backend server")

        docker_base_url = os.getenv(
            "WORKSPACE_E2E_DOCKER_API_URL",
            f"http://host.docker.internal:{port}",
        )
        yield {
            "host_base_url": f"http://127.0.0.1:{port}",
            "docker_base_url": docker_base_url,
        }
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task


@pytest_asyncio.fixture(scope="function")
async def local_agentbox_server(
    request,
    e2e_settings,
) -> AsyncGenerator[dict[str, str], None]:
    """Point the workspace module at the provider this run is exercising.

    This used to start an AgentBox manager -- a uvicorn server, a state
    database, a settings snapshot to restore, and for E2B a second process in
    another virtualenv. None of that exists now: the workspace module
    provisions in-process, so all that is left of a 260-line fixture is
    choosing a provider and telling it which images or templates to use.

    The name survives because a dozen tests request it, and its dict keeps the
    ``provider`` key for the same reason. The manager URL and key are gone,
    because there is no manager to address.
    """

    provider_name = e2e_settings.e2e_sandbox_mode
    overrides: dict[str, object] = {"workspace_provider": provider_name}
    env_updates: dict[str, str] = {
        "WORKSPACE_PROVIDER": provider_name,
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": "test-runtime-credential-key-32-bytes",
    }

    if provider_name == "docker":
        workspace_image = request.getfixturevalue("workspace_image")
        function_image = request.getfixturevalue("function_image")
        overrides.update(
            {
                "agentbox_workspace_image": workspace_image,
                "agentbox_function_image": function_image,
                "agentbox_docker_allow_mutable_images": True,
                "agentbox_add_host_gateway": True,
                "agentbox_host_alias": "host.docker.internal",
            }
        )
        env_updates.update(
            {
                "AGENTBOX_WORKSPACE_IMAGE": workspace_image,
                "AGENTBOX_FUNCTION_IMAGE": function_image,
                "AGENTBOX_DOCKER_ALLOW_MUTABLE_IMAGES": "true",
                "AGENTBOX_ADD_HOST_GATEWAY": "true",
                "AGENTBOX_HOST_ALIAS": "host.docker.internal",
            }
        )
    else:
        required = {
            "E2B_API_KEY": _e2b_environment("E2B_API_KEY"),
            "E2B_WORKSPACE_TEMPLATE": _e2b_environment(
                "AGENTBOX_E2B_WORKSPACE_TEMPLATE"
            ),
            "E2B_FUNCTION_TEMPLATE": _e2b_environment("AGENTBOX_E2B_FUNCTION_TEMPLATE"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            pytest.fail(
                "E2B workspace E2E configuration is missing: " + ", ".join(missing)
            )
        overrides.update(
            {
                "e2b_api_key": required["E2B_API_KEY"],
                "e2b_workspace_template": required["E2B_WORKSPACE_TEMPLATE"],
                "e2b_function_template": required["E2B_FUNCTION_TEMPLATE"],
            }
        )
        env_updates.update({k: v for k, v in required.items() if v})

    original_settings = {key: getattr(settings, key) for key in overrides}
    original_env = {key: os.environ.get(key) for key in env_updates}
    for key, value in overrides.items():
        setattr(settings, key, value)
    os.environ.update(env_updates)

    from app.modules.workspace.services.sandbox_composition import (
        reset_sandbox_service,
    )

    await reset_sandbox_service()
    try:
        yield {"provider": provider_name}
    finally:
        await reset_sandbox_service()
        for key, value in original_settings.items():
            setattr(settings, key, value)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest_asyncio.fixture
async def configure_workspace_api_url(
    backend_server,
    local_agentbox_server,
) -> AsyncGenerator[dict[str, str], None]:
    """Route workspace SDK calls to the backend and selected AgentBox provider."""

    from app.modules.workspace.services.workspace_tool_runtime import (
        close_workspace_tool_runtimes,
    )

    original_callback_url = settings.workspace_callback_api_url
    original_callback_url_env = os.environ.get("WORKSPACE_CALLBACK_API_URL")
    original_function_gateway_url = settings.function_runtime_gateway_url
    original_function_gateway_url_env = os.environ.get(
        "FUNCTION_RUNTIME_GATEWAY_URL"
    )

    # A sandbox running in someone else's cloud cannot reach a laptop, so the
    # backend has to be published for it. This is needed whenever the *live*
    # provisioner puts sandboxes off-box, which is no longer only a question
    # about AgentBox: the workspace module selects its own provider.
    # Read the setting rather than re-deriving its default from the
    # environment: the two disagreed the moment the cutover default flipped,
    # and the harness would then have decided the old path was live while the
    # backend it started was running the new one.
    needs_public_backend = local_agentbox_server["provider"] == "e2b"
    # A session-scoped tunnel may already be published for the worker, which
    # cannot see a per-test one. Reuse it: a second tunnel to the same port
    # would work but costs a process and a startup wait per test.
    session_tunnel = os.getenv("WORKSPACE_CALLBACK_API_URL", "")
    if needs_public_backend and session_tunnel.startswith("https://"):
        tunnel = contextlib.nullcontext(session_tunnel)
    else:
        tunnel = (
            _temporary_workspace_tunnel(backend_server["host_base_url"])
            if needs_public_backend
            else contextlib.nullcontext(None)
        )
    async with tunnel as public_callback_url:
        await close_workspace_tool_runtimes()
        workspace_callback_url = (
            public_callback_url
            if public_callback_url is not None
            else backend_server["docker_base_url"]
        )
        settings.workspace_callback_api_url = workspace_callback_url
        settings.function_runtime_gateway_url = workspace_callback_url
        os.environ["WORKSPACE_CALLBACK_API_URL"] = workspace_callback_url
        os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = workspace_callback_url
        try:
            yield {
                **backend_server,
                **local_agentbox_server,
                "workspace_callback_url": settings.workspace_callback_api_url,
            }
        finally:
            await close_workspace_tool_runtimes()
            settings.workspace_callback_api_url = original_callback_url
            settings.function_runtime_gateway_url = original_function_gateway_url
            if original_callback_url_env is None:
                os.environ.pop("WORKSPACE_CALLBACK_API_URL", None)
            else:
                os.environ["WORKSPACE_CALLBACK_API_URL"] = original_callback_url_env
            if original_function_gateway_url_env is None:
                os.environ.pop("FUNCTION_RUNTIME_GATEWAY_URL", None)
            else:
                os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = (
                    original_function_gateway_url_env
                )


@pytest_asyncio.fixture(scope="function")
async def scheduler_api_server(
    e2e_settings,
    db_manager,
) -> AsyncGenerator[str, None]:
    """Run a real scheduler API server for workflow/schedule e2e tests."""
    _ = db_manager

    from app.scheduler import app as scheduler_app

    port = _available_port()
    original_scheduler_url = schedule_settings.scheduler_api_url
    original_scheduler_env = os.environ.get("SCHEDULER_API_URL")
    schedule_settings.scheduler_api_url = f"http://127.0.0.1:{port}"
    os.environ["SCHEDULER_API_URL"] = schedule_settings.scheduler_api_url

    config = uvicorn.Config(
        app=scheduler_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                exc = server_task.exception()
                raise RuntimeError(
                    "Scheduler API server exited before startup"
                ) from exc
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Timed out starting scheduler API server")
        yield schedule_settings.scheduler_api_url
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10)
        except asyncio.TimeoutError:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task
        schedule_settings.scheduler_api_url = original_scheduler_url
        if original_scheduler_env is None:
            os.environ.pop("SCHEDULER_API_URL", None)
        else:
            os.environ["SCHEDULER_API_URL"] = original_scheduler_env


@pytest_asyncio.fixture(scope="function")
async def full_stack(
    configure_workspace_api_url,
    scheduler_api_server,
) -> AsyncGenerator[dict[str, str], None]:
    """The complete stack for fully-real e2e tests.

    Combines the real backend + local Docker AgentBox (``configure_workspace_api_url``)
    and a real scheduler (``scheduler_api_server``) with the **production streaq
    worker subprocess** wired to the AgentBox and the ``system:lemma`` agent
    runtime. The worker is a fresh subprocess per test (no shared in-process
    singletons), so triggered runs execute real functions in Docker and
    deterministic mock agents by default. Set ``E2E_LLM_MODE=real`` to use the
    configured live system:lemma provider.

    Skips only in real-LLM mode when system:lemma credentials are unavailable.
    """
    import redis.asyncio as redis

    skip_unless_system_lemma()
    env_overlay = system_lemma_env_overlay()
    system_lemma_key = system_lemma_api_key()
    coverage_env = {
        name: value
        for name in ("COVERAGE_PROCESS_START", "COVERAGE_FILE")
        if (value := os.environ.get(name))
    }

    # Make the same system:lemma env visible to both the in-process backend and
    # the worker subprocess. In default mock mode this may be empty; in real mode
    # the helper has already verified that the key exists.
    original_overlay_env = {key: os.environ.get(key) for key in env_overlay}
    original_cred_setting = settings.lemma_openai_api_key
    for key, value in env_overlay.items():
        os.environ[key] = value
    if system_lemma_key:
        settings.lemma_openai_api_key = system_lemma_key

    redis_url = settings.redis_url
    redis_client = redis.from_url(redis_url, decode_responses=False)
    await redis_client.flushdb()
    await redis_client.aclose()

    backend_root = Path(__file__).resolve().parents[4]
    log_path = f"/tmp/lemma_full_stack_worker_{uuid.uuid4().hex}.log"

    # The worker subprocess inherits os.environ, which carries API_URL (from
    # configure_workspace_api_url), SCHEDULER_API_URL (from scheduler_api_server),
    # and any system:lemma provider env from .env/CI.
    worker_env = {
        **os.environ,
        **env_overlay,
        **coverage_env,
        "PYTHONPATH": ".",
        "DATABASE_URL": settings.database_url,
        "DATASTORE_DATABASE_URL": settings.datastore_database_url,
        "REDIS_URL": redis_url,
        "SUPERTOKENS_CORE_URL": settings.supertokens_core_url,
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": settings.email_output_dir,
        "GCS_STORAGE_BUCKET": "",
        "STORAGE_BUCKET": "",
        "PUBLIC_BUCKET_NAME": "",
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_OBJECT_STORAGE_ROOT": settings.local_object_storage_root,
        "LOCAL_FILE_STORAGE_ROOT": settings.local_file_storage_root,
        "COMPOSIO_CACHE_DIR": "/tmp/composio",
    }

    with open(log_path, "w+") as log_file:
        proc = subprocess.Popen(
            [str(backend_root / ".venv/bin/streaq"), "run", "app.events:streaq_worker"],
            cwd=str(backend_root),
            env=worker_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        readiness_markers = (
            '"logger": "app.core.infrastructure.jobs.streaq_runtime"',
            '"event": "service.started"',
        )
        startup_ok = False
        for _ in range(300):
            if proc.poll() is not None:
                log_file.flush()
                log_file.seek(0)
                pytest.fail(
                    "full_stack worker exited before startup "
                    f"(code={proc.returncode}).\n{log_file.read()}"
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
            pytest.fail(f"Timed out waiting for full_stack worker.\n{log_file.read()}")

        try:
            yield {
                "host_base_url": configure_workspace_api_url["host_base_url"],
                "docker_base_url": configure_workspace_api_url["docker_base_url"],
            }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            redis_client = redis.from_url(redis_url, decode_responses=False)
            await redis_client.flushdb()
            await redis_client.aclose()
            settings.lemma_openai_api_key = original_cred_setting
            for key, value in original_overlay_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
