"""Shared E2E runtime fixtures for worker, workspace, and scheduler tests."""

from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4
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

from app.modules.datastore.config import datastore_settings
from app.modules.function.config import function_settings
from app.core.config import settings
from app.modules.workspace.providers.e2b_common import (
    DEFAULT_METADATA_NAMESPACE,
)
from app.modules.workspace.config import workspace_settings
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


# Signs the per-sandbox token the in-sandbox runtime accepts. Any stable value
# works; it only has to be the same one the worker subprocess and the
# in-process provider factory sign with.
_RUNTIME_CREDENTIAL_KEY = "test-runtime-credential-key-32-bytes"

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


# Source paths the workspace and function images are built from -- the union of
# what their Dockerfiles COPY. Keep this in step with those COPY lines: a path
# missing here caches a stale image, and a path that no longer exists makes the
# fingerprint unresolvable, which silently rebuilds the image for every test
# module.
_SANDBOX_BUILD_INPUTS = (
    "lemma-backend/sandbox-images",
    "lemma-backend/sandbox_runtime",
    "lemma-python",
    "lemma-pod-bundle",
    "lemma-cli",
    "lemma-typescript",
    "lemma-skills",
)


def _sandbox_image_fingerprint(repo_root: Path) -> str | None:
    """Short content hash of the sandbox runtime image's build inputs.

    Combines the committed git tree/blob hashes of the relevant paths with the
    current uncommitted diff and any untracked files, so the fingerprint changes
    exactly when the image would build differently — committed or not. Returns
    None when git can't be used (then the caller falls back to always building).
    """
    paths = list(_SANDBOX_BUILD_INPUTS)
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


def _workspace_image_name() -> str:
    """The workspace image tag, without building it.

    Content-addressed, so naming the image is pure and the (slow) build stays
    in the fixture. The worker subprocess needs the *name* at spawn, long
    before any module-scoped fixture has run.
    """
    configured_image = os.getenv("WORKSPACE_E2E_IMAGE")
    if configured_image:
        return configured_image
    fingerprint = _sandbox_image_fingerprint(Path(__file__).resolve().parents[5])
    return (
        f"lemma-workspace:e2e-{fingerprint}" if fingerprint else "lemma-workspace:e2e"
    )


def _function_image_name() -> str:
    """The function-runner image tag, without building it."""
    configured_image = os.getenv("FUNCTION_E2E_IMAGE")
    if configured_image:
        return configured_image
    platform = os.getenv("FUNCTION_E2E_PLATFORM", "linux/amd64")
    fingerprint = _sandbox_image_fingerprint(Path(__file__).resolve().parents[5])
    platform_tag = platform.rsplit("/", 1)[-1].replace("_", "-")
    return (
        f"lemma-function:e2e-{platform_tag}-{fingerprint}"
        if fingerprint
        else f"lemma-function:e2e-{platform_tag}"
    )


def workspace_provisioning_env() -> dict[str, str]:
    """Workspace settings the session-scoped worker subprocess must be born with.

    Sandboxes are provisioned in-process now, so the worker provisions its own
    -- it is no longer a client of a separate manager process that held this
    configuration. It captures its environment once at spawn, and
    ``local_sandbox_server`` is function-scoped, so anything set there arrives
    far too late: the worker would reject the tag-pinned E2E images for not
    being digest-pinned, and its sandboxes could not reach the host.
    """
    if workspace_settings.provider.lower() != "docker":
        return {}
    return {
        "WORKSPACE_PROVIDER": "docker",
        "WORKSPACE_IMAGE": _workspace_image_name(),
        "FUNCTION_IMAGE": _function_image_name(),
        "WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES": "true",
        "WORKSPACE_ADD_HOST_GATEWAY": "true",
        "WORKSPACE_HOST_ALIAS": "host.docker.internal",
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": _RUNTIME_CREDENTIAL_KEY,
    }


@pytest.fixture(scope="module")
def workspace_image(e2e_settings) -> Generator[str, None, None]:
    """Ensure the docker workspace runtime image exists locally.

    Uses a content-addressed tag (``sandbox-runtime:e2e-<fingerprint>``) so the
    image is reused when the sandbox images + the bundled SDKs are unchanged, and rebuilt
    automatically when they change — no per-run rebuild (the build context is the
    whole monorepo, which is slow to transfer) and no stale pinned image. Set
    WORKSPACE_E2E_IMAGE to pin an explicit image (e.g. in CI).
    """
    repo_root = Path(__file__).resolve().parents[5]
    configured_image = os.getenv("WORKSPACE_E2E_IMAGE")
    image = _workspace_image_name()

    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    image_present = inspect.returncode == 0
    # Fall back to always-build only when we couldn't fingerprint (floating tag).
    should_build = not image_present or (
        not configured_image and image == "lemma-workspace:e2e"
    )

    if should_build:
        dockerfile = (
            repo_root / "lemma-backend" / "sandbox-images" / "Dockerfile.workspace"
        )
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
    """Build the slim stateless function runner image used by the sandbox runtime.

    Function artifacts currently declare the x86_64 runtime ABI, including for
    native wheels. Build the E2E runtime for that exact platform even on arm64
    developer machines; a native arm image would accept the profile but could
    not load the artifact's compiled extensions.
    """

    del e2e_settings
    repo_root = Path(__file__).resolve().parents[5]
    platform = os.getenv("FUNCTION_E2E_PLATFORM", "linux/amd64")
    image = _function_image_name()
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
                str(
                    repo_root
                    / "lemma-backend"
                    / "sandbox-images"
                    / "Dockerfile.function"
                ),
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
async def local_sandbox_server(
    request,
    e2e_settings,
) -> AsyncGenerator[dict[str, str], None]:
    """Point the workspace module at the provider this run is exercising.

    Selects Docker or E2B and supplies the images or templates it needs. The
    name is historical -- a dozen tests request it -- and its dict keeps the
    ``provider`` key those tests read.
    """

    provider_name = e2e_settings.e2e_sandbox_mode
    # Attribute names on WorkspaceSettings, not the env names below.
    #
    # The credential key has to appear in both. The env copy is inherited by
    # the worker subprocess; the attribute is what the in-process provider
    # factory reads, and without it every provisioning call raises
    # "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required" no matter what the
    # environment says.
    overrides: dict[str, object] = {
        "provider": provider_name,
        "runtime_credential_key": _RUNTIME_CREDENTIAL_KEY,
    }
    env_updates: dict[str, str] = {
        "WORKSPACE_PROVIDER": provider_name,
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY": _RUNTIME_CREDENTIAL_KEY,
    }

    if provider_name == "docker":
        workspace_image = request.getfixturevalue("workspace_image")
        function_image = request.getfixturevalue("function_image")
        overrides.update(
            {
                "workspace_image": workspace_image,
                "function_image": function_image,
                "docker_allow_mutable_images": True,
                "add_host_gateway": True,
                "host_alias": "host.docker.internal",
            }
        )
        env_updates.update(
            {
                "WORKSPACE_IMAGE": workspace_image,
                "FUNCTION_IMAGE": function_image,
                "WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES": "true",
                "WORKSPACE_ADD_HOST_GATEWAY": "true",
                "WORKSPACE_HOST_ALIAS": "host.docker.internal",
            }
        )
    else:
        required = {
            "E2B_API_KEY": _e2b_environment("E2B_API_KEY"),
            "E2B_WORKSPACE_TEMPLATE": _e2b_environment("E2B_WORKSPACE_TEMPLATE"),
            "E2B_FUNCTION_TEMPLATE": _e2b_environment("E2B_FUNCTION_TEMPLATE"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            pytest.fail(
                "E2B workspace E2E configuration is missing: " + ", ".join(missing)
            )
        # Never production's namespace. The orphan sweep destroys any provider
        # object it can identify as ours that has no sandbox row -- and this run
        # owns a throwaway database in which no real workspace has one, so
        # sharing the namespace with a live account means the sweep deletes live
        # workspaces. A per-run value makes those sandboxes invisible to it.
        namespace = _e2b_environment("E2B_METADATA_NAMESPACE") or (
            f"lemma-e2e-{uuid4().hex[:12]}"
        )
        if namespace == DEFAULT_METADATA_NAMESPACE:
            pytest.fail(
                "E2B E2E refuses to run in the production metadata namespace "
                f"({DEFAULT_METADATA_NAMESPACE!r}): the orphan sweep would treat "
                "every sandbox in this account as unowned and destroy it. Unset "
                "E2B_METADATA_NAMESPACE to get a per-run namespace."
            )
        overrides.update(
            {
                "e2b_api_key": required["E2B_API_KEY"],
                "e2b_workspace_template": required["E2B_WORKSPACE_TEMPLATE"],
                "e2b_function_template": required["E2B_FUNCTION_TEMPLATE"],
                "e2b_metadata_namespace": namespace,
            }
        )
        env_updates.update({k: v for k, v in required.items() if v})
        env_updates["E2B_METADATA_NAMESPACE"] = namespace

    original_settings = {key: getattr(workspace_settings, key) for key in overrides}
    original_env = {key: os.environ.get(key) for key in env_updates}
    for key, value in overrides.items():
        setattr(workspace_settings, key, value)
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
            setattr(workspace_settings, key, value)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest_asyncio.fixture
async def configure_workspace_api_url(
    backend_server,
    local_sandbox_server,
) -> AsyncGenerator[dict[str, str], None]:
    """Route workspace SDK calls to the backend and selected the sandbox runtime provider."""

    from app.modules.workspace.services.workspace_tool_runtime import (
        close_workspace_tool_runtimes,
    )

    original_callback_url = settings.workspace_callback_api_url
    original_callback_url_env = os.environ.get("WORKSPACE_CALLBACK_API_URL")
    original_function_gateway_url = function_settings.function_runtime_gateway_url
    original_function_gateway_url_env = os.environ.get("FUNCTION_RUNTIME_GATEWAY_URL")

    # A sandbox running in someone else's cloud cannot reach a laptop, so the
    # backend has to be published for it. This is needed whenever the *live*
    # provisioner puts sandboxes off-box, which is no longer only a question
    # about the sandbox runtime: the workspace module selects its own provider.
    # Read the setting rather than re-deriving its default from the
    # environment: the two disagreed the moment the cutover default flipped,
    # and the harness would then have decided the old path was live while the
    # backend it started was running the new one.
    needs_public_backend = local_sandbox_server["provider"] == "e2b"
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
        function_settings.function_runtime_gateway_url = workspace_callback_url
        os.environ["WORKSPACE_CALLBACK_API_URL"] = workspace_callback_url
        os.environ["FUNCTION_RUNTIME_GATEWAY_URL"] = workspace_callback_url
        try:
            yield {
                **backend_server,
                **local_sandbox_server,
                "workspace_callback_url": settings.workspace_callback_api_url,
            }
        finally:
            await close_workspace_tool_runtimes()
            settings.workspace_callback_api_url = original_callback_url
            function_settings.function_runtime_gateway_url = (
                original_function_gateway_url
            )
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
async def full_stack(
    configure_workspace_api_url,
) -> AsyncGenerator[dict[str, str], None]:
    """The complete stack for fully-real e2e tests.

    Combines the real backend + local Docker sandbox (``configure_workspace_api_url``)
    and the **production streaq
    worker subprocess** wired to the sandbox and the ``system:lemma`` agent
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
    # configure_workspace_api_url),
    # and any system:lemma provider env from .env/CI.
    worker_env = {
        **os.environ,
        **env_overlay,
        **coverage_env,
        "PYTHONPATH": ".",
        "DATABASE_URL": settings.database_url,
        "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url,
        "REDIS_URL": redis_url,
        "SUPERTOKENS_CORE_URL": settings.supertokens_core_url,
        "ENVIRONMENT": "testing",
        # E2E workers have no production drain to protect, and the default
        # 10s grace period equalled the teardown's patience -- so the worker
        # spent its whole grace draining, overran, and got SIGKILLed. SIGKILL
        # cannot be trapped, so coverage's `sigterm = true` handler never
        # flushed and the subprocess's coverage was lost.
        "WORKER_SHUTDOWN_GRACE_PERIOD_SECONDS": "1",
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
            [str(backend_root / ".venv/bin/python"), "-m", "app.worker"],
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
                # Comfortably longer than the 1s grace period set at spawn, so
                # SIGKILL becomes unreachable in practice.
                proc.wait(timeout=30)
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
