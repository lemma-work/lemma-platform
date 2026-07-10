"""Build + deploy a bundled app into the target pod on import.

An app is the one resource that cannot be applied by the ordinary :class:`~app.
modules.pod_bundle.infrastructure.applier.BundleApplier` step loop, for two
reasons:

* **It must be rebuilt.** A Vite app bakes ``VITE_LEMMA_POD_ID`` (and the API
  URLs) into its bundle at build time, and its ``public_slug`` is unique
  platform-wide — both change when the pod is imported elsewhere, so a prebuilt
  dist from the source pod is useless. The rebuild runs ``npm install && npm run
  build`` inside the importing user's AgentBox sandbox with the *target* pod's
  values. (A static/no-``package.json`` app needs no rebuild — the host injects
  ``window.__LEMMA_CONFIG__`` at serve time — so its files are deployed as-is.)
* **It must hold no DB connection across the build.** The build + storage upload
  take minutes and touch the sandbox and object storage; holding a pooled
  connection across them would violate the pool-safety discipline the whole
  pod-bundle feature enforces. So the step opens its *own* short units of work
  around the connectionless build (like ``AppUseCases.upload_bundle``), which is
  why the apply loop hands it a ``uow_factory`` instead of running it inside the
  shared per-step ``uow_scope``.

:class:`AppStepRunner` owns the create → build → upload sequencing and the short
UoW boundaries; :class:`AppSandboxBuilder` owns the (DB-free) agentbox build.
Both take injected collaborators so the step is unit-testable with a fake
workspace session and a fake app service.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import os
import shlex
import zipfile
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from app.core.concurrency.offload import run_blocking
from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import AppBuildFailedError

logger = get_logger(__name__)

# npm install + build for a modest frontend can take a few minutes; give it room
# but bound it so a hung build fails the step instead of the whole job's timeout.
_BUILD_TIMEOUT_SECONDS = int(os.getenv("LEMMA_POD_BUNDLE_APP_BUILD_TIMEOUT_SECONDS", "600"))
_IO_TIMEOUT_SECONDS = 120
# base64 payloads are written to the sandbox in chunks to stay well under ARG_MAX.
_B64_CHUNK = 60_000
# Source-tree entries never worth shipping/rebuilding from (mirrors the CLI's
# _should_exclude_source_path so an export→import round-trips cleanly).
_SOURCE_EXCLUDE_DIRS = frozenset(
    {"node_modules", ".git", "dist", "build", ".next", ".turbo", ".cache", "coverage", "__pycache__"}
)


# --- pure helpers (unit-testable without a DB or a sandbox) -------------------


def classify_source_dir(source_dir: Path) -> str:
    """``"vite"`` when the source is a buildable project (has ``package.json``),
    ``"static"`` when it is a prebuilt site (has ``index.html`` at its root)."""
    if (source_dir / "package.json").is_file():
        return "vite"
    if (source_dir / "index.html").is_file():
        return "static"
    raise AppBuildFailedError(
        f"App source in '{source_dir.name}' has neither a package.json (buildable) "
        "nor an index.html (static site)."
    )


def zip_dir(source_dir: Path, *, exclude_build_dirs: bool = True) -> bytes:
    """Zip a directory tree to bytes, skipping build/vcs junk. ``index.html`` (or
    any file) keeps its path relative to ``source_dir`` so a static site's entry
    point lands at the archive root."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(source_dir)
            if exclude_build_dirs and any(part in _SOURCE_EXCLUDE_DIRS for part in rel.parts):
                continue
            archive.write(path, arcname=rel.as_posix())
    return buffer.getvalue()


def slug_candidates(preferred: str | None, *, pod_id: UUID, app_name: str) -> list[str]:
    """Deterministic ordered slug candidates: the preferred/normalized base first,
    then a stable ``(pod_id, app_name)`` hash suffix. Deterministic — never random
    — so a retry after a crash resolves to the *same* slug and converges instead of
    minting a second app."""
    from app.core.helpers.slug import normalize_public_slug

    base = normalize_public_slug(preferred or "") or normalize_public_slug(app_name) or "app"
    digest = hashlib.sha256(f"{pod_id}:{app_name}".encode()).hexdigest()[:6]
    candidates = [base, f"{base}-{digest}"]
    candidates.extend(f"{base}-{digest}{i}" for i in range(2, 8))
    return candidates


def _chunks(text: str, size: int):
    for start in range(0, len(text), size):
        yield text[start : start + size]


# --- agentbox build (no DB connection) ---------------------------------------


@contextlib.asynccontextmanager
async def _keep_sandbox_alive(session: Any):
    """Heartbeat the session's sandbox so the idle reaper does not delete it while
    a multi-minute build runs. Best-effort; no-ops when the session has no manager
    client (e.g. in tests). Mirrors ``FunctionRunExecutor._keep_sandbox_alive``."""
    import asyncio

    sandbox_id = getattr(session, "sandbox_id", None)
    client = getattr(session, "client", None)
    heartbeat = getattr(client, "heartbeat_sandbox", None)
    if not sandbox_id or heartbeat is None:
        yield
        return

    interval = int(os.getenv("LEMMA_SANDBOX_HEARTBEAT_INTERVAL_SECONDS", "30"))

    async def _loop() -> None:
        first = True
        while True:
            if not first:
                await asyncio.sleep(interval)
            first = False
            try:
                await heartbeat(sandbox_id)
            except Exception as exc:  # noqa: BLE001 - best-effort keepalive
                logger.debug("app-build sandbox heartbeat failed %s: %s", sandbox_id, exc)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class AppSandboxBuilder:
    """Runs the Vite build for an app inside the importing user's AgentBox and
    returns the built ``dist`` archive bytes. Holds no DB connection."""

    def __init__(self, workspace_service: Any | None = None):
        self._workspace = workspace_service

    def _service(self) -> Any:
        if self._workspace is not None:
            return self._workspace
        from app.composition.pod_bundle_apps import (
            WorkspaceSandboxService,
        )

        self._workspace = WorkspaceSandboxService()
        return self._workspace

    async def build(
        self, *, user_id: UUID, pod_id: UUID, app_slug: str, source_zip: bytes
    ) -> bytes:
        """Upload the source, run ``<pm> install && <pm> run build`` with the target
        pod's ``VITE_*`` env, and return the built ``dist`` archive bytes. Raises
        :class:`AppBuildFailedError` on a non-zero build or a missing
        ``dist/index.html``."""
        env = {
            "VITE_LEMMA_API_URL": settings.api_url,
            "VITE_LEMMA_AUTH_URL": settings.auth_frontend_url,
            "VITE_LEMMA_POD_ID": str(pod_id),
        }
        session = await self._service().get_session(
            user_id=user_id,
            pod_id=pod_id,
            session_id=f"app-build-{app_slug}",
            initial_cwd="/workspace",
            close_on_exit=False,
            workload_type="pod_bundle_app_build",
            env_vars=env,
        )
        build_dir = f"/workspace/.lemma-app-build/{app_slug}"
        src_dir = f"{build_dir}/src"
        async with session:
            async with _keep_sandbox_alive(session):
                await self._sh(session, f"rm -rf {shlex.quote(build_dir)} && mkdir -p {shlex.quote(src_dir)}")
                await self._upload(session, source_zip, f"{build_dir}/source.zip")
                await self._sh(
                    session,
                    f"cd {shlex.quote(build_dir)} && unzip -oq source.zip -d src",
                )
                await self._run_build(session, src_dir)
                dist_index = f"{src_dir}/dist/index.html"
                check = await session.exec_command(
                    cmd=f"test -f {shlex.quote(dist_index)}", timeout=_IO_TIMEOUT_SECONDS
                )
                if not check.get("success"):
                    raise AppBuildFailedError(
                        f"App '{app_slug}' build produced no dist/index.html."
                    )
                await self._sh(
                    session,
                    f"cd {shlex.quote(src_dir)}/dist && zip -rq {shlex.quote(build_dir)}/dist.zip .",
                )
                return await self._download(session, f"{build_dir}/dist.zip")

    async def _run_build(self, session: Any, src_dir: str) -> None:
        # Honor a checked-in lockfile's package manager, else npm (always present).
        script = (
            f"cd {shlex.quote(src_dir)} && "
            "if [ -f pnpm-lock.yaml ]; then pnpm install && pnpm run build; "
            "elif [ -f yarn.lock ]; then yarn install && yarn build; "
            "else npm install && npm run build; fi"
        )
        result = await session.exec_command(cmd=script, timeout=_BUILD_TIMEOUT_SECONDS)
        if not result.get("success"):
            raise AppBuildFailedError(
                "App build failed.", details={"log": _tail(result)}
            )

    async def _sh(self, session: Any, cmd: str) -> None:
        result = await session.exec_command(cmd=cmd, timeout=_IO_TIMEOUT_SECONDS)
        if not result.get("success"):
            raise AppBuildFailedError(
                "App build step failed.", details={"log": _tail(result)}
            )

    async def _upload(self, session: Any, data: bytes, remote_path: str) -> None:
        """Write bytes to a sandbox path via chunked base64 (no ARG_MAX blowups)."""
        encoded = base64.b64encode(data).decode("ascii")
        b64_path = f"{remote_path}.b64"
        await self._sh(session, f": > {shlex.quote(b64_path)}")
        for chunk in _chunks(encoded, _B64_CHUNK):
            await self._sh(session, f"printf %s {shlex.quote(chunk)} >> {shlex.quote(b64_path)}")
        await self._sh(
            session,
            f"base64 -d {shlex.quote(b64_path)} > {shlex.quote(remote_path)} "
            f"&& rm -f {shlex.quote(b64_path)}",
        )

    async def _download(self, session: Any, remote_path: str) -> bytes:
        # `base64 <file> | tr -d '\n'` encodes portably (GNU + BSD).
        result = await session.exec_command(
            cmd=f"base64 {shlex.quote(remote_path)} | tr -d '\\n'",
            timeout=_IO_TIMEOUT_SECONDS,
        )
        if not result.get("success"):
            raise AppBuildFailedError(
                "Could not read the built app dist.", details={"log": _tail(result)}
            )
        return base64.b64decode(result.get("stdout") or "")


def _tail(result: dict[str, Any], limit: int = 4000) -> str:
    text = (result.get("stderr") or "") or (result.get("stdout") or "") or (result.get("error") or "")
    return text[-limit:]


# --- step runner (owns the short UoW boundaries) -----------------------------


AppServiceBuilder = Callable[[Any], Any]


class AppStepRunner:
    """Applies an ``APP`` plan step end to end, opening its own short units of work
    around a connectionless build. Used by the apply loop in place of the shared
    per-step ``uow_scope`` for APP steps."""

    def __init__(
        self,
        *,
        uow_factory: Any,
        workspace_service: Any | None = None,
        service_builder: AppServiceBuilder | None = None,
        sandbox_builder: AppSandboxBuilder | None = None,
    ):
        self._uow_factory = uow_factory
        self._build_service = service_builder or _default_service_builder
        self._sandbox = sandbox_builder or AppSandboxBuilder(workspace_service)

    async def run(
        self,
        step: Any,
        *,
        pod_id: UUID,
        user_id: UUID,
        bundle_root: Path,
        replacements: dict[str, str],
    ) -> None:
        resource_dir = bundle_root / "apps" / step.name
        manifest = self._load_manifest(resource_dir, step.name, replacements)

        # Phase 1 (short UoW): ensure the app exists with a unique slug.
        already_ready, app_slug = await self._ensure_app(
            step.name, manifest, pod_id=pod_id, user_id=user_id
        )
        if already_ready:
            # Cheap replay: a prior attempt already created + deployed this app.
            return

        # Phase 2 (no DB connection): assemble source + dist bytes (building in the
        # agentbox for a Vite app, baking the deployed slug).
        source_bytes, dist_bytes = await self._artifacts(
            resource_dir, step.name, app_slug=app_slug, pod_id=pod_id, user_id=user_id
        )

        # Phase 3 (short UoWs + storage, no connection held across the write).
        await self._deploy(
            step.name, source_bytes, dist_bytes, pod_id=pod_id, user_id=user_id
        )

    # --- phase 1 ---------------------------------------------------------

    async def _ensure_app(
        self,
        name: str,
        manifest: dict[str, Any],
        *,
        pod_id: UUID,
        user_id: UUID,
    ) -> tuple[bool, str]:
        """Create the app if absent (allocating a unique slug); return
        ``(already_ready, slug)`` where ``already_ready`` is True when it already
        exists fully deployed (a READY current release) so the caller can skip the
        rebuild on replay. ``slug`` is the app's deployed public slug."""
        from app.modules.apps.contracts import AppConflictError, AppEntity, AppStatus

        async with self._authed_scope(pod_id, user_id) as (uow, ctx):
            service = self._build_service(uow)
            existing = await service.get_app_by_name(
                pod_id, name, user_id, raise_not_found=False, ctx=ctx
            )
            if existing is not None:
                ready = (
                    existing.status == AppStatus.READY
                    and existing.current_release_id is not None
                )
                return ready, existing.public_slug

            preferred = _clean_slug(manifest.get("public_slug"))
            last_exc: Exception | None = None
            for slug in slug_candidates(preferred, pod_id=pod_id, app_name=name):
                try:
                    created = await service.create_app_with_context(
                        AppEntity(
                            pod_id=pod_id,
                            user_id=user_id,
                            name=name,
                            public_slug=slug,
                            description=manifest.get("description"),
                            visibility=manifest.get("visibility") or "POD",
                        ),
                        user_id,
                        ctx=ctx,
                    )
                    await uow.commit()
                    return False, (created.public_slug if created else slug)
                except AppConflictError as exc:
                    # The name is free (checked above), so this is a slug collision
                    # — try the next deterministic candidate.
                    last_exc = exc
                    continue
            raise AppBuildFailedError(
                f"Could not allocate a unique public slug for app '{name}'.",
                details={"error": str(last_exc) if last_exc else None},
            )

    # --- phase 2 ---------------------------------------------------------

    async def _artifacts(
        self, resource_dir: Path, name: str, *, app_slug: str, pod_id: UUID, user_id: UUID
    ) -> tuple[bytes | None, bytes]:
        """Produce ``(source_bytes, dist_bytes)`` for upload. A Vite source is built
        in the agentbox; a static source is deployed as-is; a bundle carrying only a
        prebuilt ``dist.zip`` (widget/no-source app) is uploaded with no source."""
        source_dir = resource_dir / "source"
        dist_zip = resource_dir / "dist.zip"

        if source_dir.is_dir():
            source_bytes = await run_blocking(zip_dir, source_dir, limiter="cpu_bound")
            tier = classify_source_dir(source_dir)
            if tier == "vite":
                dist_bytes = await self._sandbox.build(
                    user_id=user_id, pod_id=pod_id, app_slug=app_slug, source_zip=source_bytes
                )
            else:  # static: the source *is* the served site.
                dist_bytes = source_bytes
            return source_bytes, dist_bytes

        if dist_zip.is_file():
            dist_bytes = await run_blocking(dist_zip.read_bytes, limiter="cpu_bound")
            return None, dist_bytes

        raise AppBuildFailedError(
            f"App '{name}' bundle has neither a source/ directory nor a dist.zip."
        )

    # --- phase 3 ---------------------------------------------------------

    async def _deploy(
        self,
        name: str,
        source_bytes: bytes | None,
        dist_bytes: bytes,
        *,
        pod_id: UUID,
        user_id: UUID,
    ) -> None:
        """resolve → write (no connection) → finalize, mirroring
        ``AppUseCases.upload_bundle`` so the imported app serves immediately.
        Wraps dist validation into a terminal build error."""
        from app.modules.apps.contracts import AppValidationError

        async with self._authed_scope(pod_id, user_id) as (uow, ctx):
            service = self._build_service(uow)
            try:
                plan = await service.resolve_upload_bundle(
                    pod_id,
                    name,
                    user_id,
                    has_source=source_bytes is not None,
                    dist_archive_bytes=dist_bytes,
                    ctx=ctx,
                )
            except AppValidationError as exc:
                raise AppBuildFailedError(
                    f"App '{name}' produced an invalid dist bundle: {exc}"
                ) from exc
            await uow.commit()

        # Storage write holds no DB connection.
        written = await service.write_bundle_storage(plan, source_bytes, dist_bytes)

        async with self._authed_scope(pod_id, user_id) as (uow, ctx):
            service = self._build_service(uow)
            await service.finalize_upload_bundle(plan, written, user_id)
            await uow.commit()

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _load_manifest(
        resource_dir: Path, name: str, replacements: dict[str, str]
    ) -> dict[str, Any]:
        from lemma_pod_bundle import load_resource_payload

        from app.modules.pod_bundle.infrastructure.applier import _substitute

        payload = load_resource_payload(resource_dir, name, resource_type="apps")
        return _substitute(payload, replacements)

    @contextlib.asynccontextmanager
    async def _authed_scope(self, pod_id: UUID, user_id: UUID):
        """Open a short UoW + build the importing user's Context (mirrors the apply
        job's ``_record_recipe``). The caller commits explicitly before relying on
        the writes downstream."""
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                yield uow, ctx


def _default_service_builder(uow: Any) -> Any:
    from app.composition.pod_bundle_apps import build_app_service

    return build_app_service(uow)


def _clean_slug(value: Any) -> str | None:
    """A manifest ``public_slug`` value usable as a slug preference: drop it when it
    is empty or still an unresolved ``${var}`` placeholder (the app-slug variable was
    not supplied and had no default), so the app name is used as the base instead."""
    if not isinstance(value, str) or not value.strip() or "${" in value:
        return None
    return value
