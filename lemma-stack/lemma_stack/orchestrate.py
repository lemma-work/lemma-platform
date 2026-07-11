"""Shared bring-up / bring-down used by both the `install` CLI command and the
desktop `supervise` bridge, so they cannot drift apart."""

from __future__ import annotations

from typing import Callable, Optional

from tomlkit import TOMLDocument

from lemma_stack.config import render, store
from lemma_stack.context import AdminContext
from lemma_stack.output import AdminError, warn
from lemma_stack.paths import LocalPaths
from lemma_stack.register import register_local_server
from lemma_stack.release import manifest as release_manifest
from lemma_stack.release.manifest import ReleaseManifest
from lemma_stack.runtime import detect
from lemma_stack.runtime.base import Runtime
from lemma_stack.stack import images, lifecycle

# progress(stage, detail): stage is one of
#   "runtime", "release", "pull", "service:<name>", "migrate", "ready"
Progress = Callable[[str, str], None]


def _noop(stage: str, detail: str) -> None:  # pragma: no cover - default
    pass


def resolve_manifest(
    config: TOMLDocument,
    paths: LocalPaths,
    *,
    manifest_path=None,
    channel: Optional[str] = None,
    prefer_pinned: bool = False,
) -> ReleaseManifest:
    if manifest_path is not None:
        return release_manifest.load_file(manifest_path)
    selected_channel = channel or store.channel(config)
    has_pin = paths.release_file.exists()

    # Desktop starts follow the stable channel so replacing the app upgrades
    # its local images. Explicit version channels remain pinned. If Stable is
    # temporarily unreachable, use the last known-good manifest so local mode
    # still works offline.
    if prefer_pinned and has_pin and selected_channel != "stable":
        return release_manifest.load_pinned(paths)
    try:
        return release_manifest.fetch(selected_channel)
    except AdminError:
        if not (prefer_pinned and has_pin and selected_channel == "stable"):
            raise
        pinned = release_manifest.load_pinned(paths)
        warn(f"could not refresh the stable release; continuing with pinned Lemma {pinned.version}")
        return pinned


def bring_up(
    paths: LocalPaths,
    config: TOMLDocument,
    *,
    provider: str,
    manifest: ReleaseManifest,
    do_register: bool = True,
    progress: Progress = _noop,
) -> Runtime:
    """Pull images, start every service in order, optionally register the CLI.

    Idempotent: reconcile-by-hash means an already-running stack is a no-op,
    and migrations (alembic upgrade head) are safe to re-run.
    """
    progress("runtime", f"preparing {provider}")
    runtime = detect.ensure_ready(provider)

    progress("pull", f"fetching Lemma {manifest.version}")
    images.pull_release(runtime, manifest, kreuzberg=store.feature(config, "kreuzberg"))
    release_manifest.pin(paths, manifest)

    ctx = AdminContext(paths=paths, config=config)
    lifecycle.up(
        runtime,
        ctx.specs(manifest),
        manifest,
        migrate=True,
        on_progress=progress,
    )

    if do_register:
        register_local_server(
            base_url=render.backend_origin(config),
            auth_url=f"{render.frontend_origin(config)}/auth",
            make_active=True,
        )
    return runtime


def bring_down(paths: LocalPaths, config: TOMLDocument, *, infra: bool = False) -> None:
    ctx = AdminContext(paths=paths, config=config)
    specs = ctx.specs()
    if not infra:
        specs = [s for s in specs if s.name in {"agentbox", "backend", "frontend"}]
    lifecycle.down(ctx.runtime, specs)
