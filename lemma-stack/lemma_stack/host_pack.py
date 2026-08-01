"""Render a concrete two-process host-pack manifest for ``lemma-locald``."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from tomlkit import TOMLDocument

from lemma_stack.config import render, store
from lemma_stack.output import AdminError
from lemma_stack.paths import LocalPaths
from lemma_stack.release.manifest import ReleaseManifest

HOST_PACK_SCHEMA_VERSION = 1


def _required_file(root: Path, label: str, candidates: tuple[str, ...]) -> Path:
    for candidate in candidates:
        path = root / candidate
        if path.is_file():
            return path.resolve()
    expected = ", ".join(candidates)
    raise AdminError(f"host pack is missing {label}; expected one of: {expected}")


def _required_dir(root: Path, label: str, relative: str) -> Path:
    path = root / relative
    if not path.is_dir():
        raise AdminError(f"source checkout is missing {label}: {path}")
    return path.resolve()


@dataclass(frozen=True)
class _Bindings:
    """Everything about a manifest that depends on *where the code lives*.

    A released host pack ships its own interpreter, its own Node, and a built
    Next server. A developer's checkout has none of those and uses ``uv`` and
    ``next dev`` instead. Those are the only differences: ports, environment,
    the managed-runtime block, health checks and restart policy are all
    identical, so they are rendered once from these bindings rather than
    duplicated into a second code path. A dev run that does not exercise the
    same supervisor proves nothing about the packaged one.
    """

    # Argv prefix that runs the backend's Python, e.g. the packed interpreter
    # or ``uv run --project …``.
    python: list[str]
    backend_dir: Path
    frontend_command: list[str]
    frontend_dir: Path
    # Where the backend finds assets that are baked into a pack but scattered
    # across sibling projects in a checkout.
    browser_sdk: Path
    browser_ui: Path
    skills: Path


def _packaged_bindings(root: Path) -> _Bindings:
    python = _required_file(
        root,
        "backend Python",
        (
            "backend/python/bin/python3",
            "backend/python/bin/python",
            "backend/python/python.exe",
        ),
    )
    node = _required_file(
        root,
        "frontend Node.js",
        ("frontend/node/bin/node", "frontend/node/node.exe"),
    )
    frontend_launcher = _required_file(
        root,
        "frontend launcher",
        ("frontend/frontend-launcher.mjs",),
    )
    frontend_server = _required_file(
        root,
        "Next.js standalone server",
        (
            "frontend/server.js",
            "frontend/app/server.js",
            "frontend/lemma-frontend/server.js",
        ),
    )
    backend_dir = (root / "backend").resolve()
    return _Bindings(
        python=[str(python)],
        backend_dir=backend_dir,
        frontend_command=[str(node), str(frontend_launcher), str(frontend_server)],
        frontend_dir=(root / "frontend").resolve(),
        browser_sdk=backend_dir / "assets/browser-sdk/lemma-client.js",
        browser_ui=backend_dir / "assets/browser-sdk/lemma-ui.js",
        skills=backend_dir / "assets/lemma-skills",
    )


def _source_bindings(root: Path) -> _Bindings:
    """Run the working tree instead of a released pack.

    ``uv`` and ``node`` come from the developer's PATH, which is the point:
    there is nothing to build or stage before local mode runs the code being
    edited. The frontend uses the same launcher as a pack so that
    ``runtime-config.js`` — locald's frontend health check — is still written
    by exactly one piece of code.
    """
    backend_dir = _required_dir(root, "the backend project", "lemma-backend")
    frontend_dir = _required_dir(root, "the frontend project", "lemma-frontend")
    launcher = _required_file(
        root,
        "frontend launcher",
        ("desktop/runtime/frontend-launcher.mjs",),
    )
    return _Bindings(
        python=["uv", "run", "--project", str(backend_dir), "python"],
        backend_dir=backend_dir,
        frontend_command=["node", str(launcher), "--dev", str(frontend_dir)],
        frontend_dir=frontend_dir,
        browser_sdk=root / "lemma-typescript/public/lemma-client.js",
        browser_ui=root / "lemma-typescript/public/lemma-ui.js",
        skills=root / "lemma-skills",
    )


def build_manifest(
    pack_root: Path | None,
    paths: LocalPaths,
    config: TOMLDocument,
    release: ReleaseManifest,
    *,
    provider: str | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Render the manifest locald supervises.

    ``source_root`` renders the same manifest against a developer's checkout
    rather than a released pack, so desktop local mode runs the code being
    edited. Only the paths differ; see :class:`_Bindings`.
    """
    if source_root is not None:
        root = source_root.expanduser().resolve()
        if not root.is_dir():
            raise AdminError(f"source root does not exist: {root}")
        bindings = _source_bindings(root)
    elif pack_root is not None:
        root = pack_root.expanduser().resolve()
        if not root.is_dir():
            raise AdminError(f"host pack root does not exist: {root}")
        bindings = _packaged_bindings(root)
    else:
        raise AdminError("build_manifest needs either a pack root or a source root")

    python = bindings.python
    backend_dir = bindings.backend_dir
    frontend_dir = bindings.frontend_dir
    selected_provider = provider or store.provider(config)

    backend_env = render.host_backend_env(
        config,
        paths,
        provider=selected_provider,
        workspace_image=release.image("agentbox_workspace").pull_ref,
        function_image=release.image("agentbox_function").pull_ref,
    )
    browser_sdk = bindings.browser_sdk
    browser_ui = bindings.browser_ui
    skills = bindings.skills
    if browser_sdk.is_file():
        backend_env["BROWSER_SDK_PATH"] = str(browser_sdk)
    if browser_ui.is_file():
        backend_env["BROWSER_UI_PATH"] = str(browser_ui)
    if skills.is_dir():
        backend_env["LEMMA_SKILLS_ROOT"] = str(skills)

    managed_runtime = None
    if selected_provider == "lemma_local":
        postgres_password = os.environ["LEMMA_MANAGED_POSTGRES_PASSWORD"]
        redis_password = os.environ["LEMMA_MANAGED_REDIS_PASSWORD"]
        managed_runtime = {
            "images": {
                name: release.infra_image(name) for name in ("postgres", "redis", "supertokens")
            },
            "credentials": {
                "postgres_password": postgres_password,
                "redis_password": redis_password,
            },
            "ports": {
                "postgres": store.port(config, "postgres"),
                "redis": store.port(config, "redis"),
                "supertokens": store.port(config, "supertokens"),
                "backend": store.port(config, "backend"),
                "frontend": store.port(config, "frontend"),
            },
        }

    return {
        "schema_version": HOST_PACK_SCHEMA_VERSION,
        "release": release.version,
        "managed_runtime": managed_runtime,
        "setup": [
            {
                "id": "migrations",
                "command": [
                    *python,
                    "-m",
                    "alembic",
                    "-c",
                    "alembic.ini",
                    "upgrade",
                    "head",
                ],
                "cwd": str(backend_dir),
                "env": backend_env,
                "timeout_seconds": 300,
                "max_attempts": 3,
                "retry_backoff_seconds": 2,
            },
            {
                "id": "agentbox-migrations",
                "command": [
                    *python,
                    "-m",
                    "alembic",
                    "-c",
                    "agentbox-alembic.ini",
                    "upgrade",
                    "head",
                ],
                "cwd": str(backend_dir),
                "env": backend_env,
                "timeout_seconds": 300,
                "max_attempts": 3,
                "retry_backoff_seconds": 2,
            },
        ],
        "services": [
            {
                "id": "backend",
                "command": [
                    *python,
                    "-m",
                    "uvicorn",
                    "local_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(store.port(config, "backend")),
                    "--ws",
                    "websockets-sansio",
                ],
                "cwd": str(backend_dir),
                "env": backend_env,
                "dependencies": [],
                "health": {
                    "url": f"http://127.0.0.1:{store.port(config, 'backend')}/health/ready",
                    "timeout_seconds": 180,
                },
                "restart": {
                    "max_restarts": 3,
                    "window_seconds": 60,
                    "backoff_seconds": 2,
                },
            },
            {
                "id": "frontend",
                "command": list(bindings.frontend_command),
                "cwd": str(frontend_dir),
                "env": render.host_frontend_env(config),
                "dependencies": ["backend"],
                "health": {
                    # locald binds every service health check to the current
                    # runtime generation. The generated runtime-config payload
                    # carries that identity; the HTML shell does not.
                    "url": (
                        f"http://127.0.0.1:{store.port(config, 'frontend')}"
                        "/runtime-config.js"
                    ),
                    "timeout_seconds": 120,
                },
                "restart": {
                    "max_restarts": 3,
                    "window_seconds": 60,
                    "backoff_seconds": 2,
                },
            },
        ],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path
