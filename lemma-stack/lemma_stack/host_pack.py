"""Render a concrete two-process host-pack manifest for ``lemma-locald``."""

from __future__ import annotations

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


def build_manifest(
    pack_root: Path,
    paths: LocalPaths,
    config: TOMLDocument,
    release: ReleaseManifest,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    root = pack_root.expanduser().resolve()
    if not root.is_dir():
        raise AdminError(f"host pack root does not exist: {root}")

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
    frontend_dir = (root / "frontend").resolve()
    selected_provider = provider or store.provider(config)

    backend_env = render.host_backend_env(
        config,
        paths,
        provider=selected_provider,
        workspace_image=release.image("agentbox_workspace").pull_ref,
        function_image=release.image("agentbox_function").pull_ref,
    )
    browser_sdk = backend_dir / "assets/browser-sdk/lemma-client.js"
    browser_ui = backend_dir / "assets/browser-sdk/lemma-ui.js"
    skills = backend_dir / "assets/lemma-skills"
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
                    str(python),
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
            }
        ],
        "services": [
            {
                "id": "backend",
                "command": [
                    str(python),
                    "-m",
                    "uvicorn",
                    "local_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(store.port(config, "backend")),
                    "--ws",
                    "websockets-sansio",
                    "--no-access-log",
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
                "command": [
                    str(node),
                    str(frontend_launcher),
                    str(frontend_server),
                ],
                "cwd": str(frontend_dir),
                "env": render.host_frontend_env(config),
                "dependencies": ["backend"],
                "health": {
                    "url": f"http://127.0.0.1:{store.port(config, 'frontend')}/",
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
