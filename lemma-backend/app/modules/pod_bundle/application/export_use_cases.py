"""Application/use-case layer for the pod-bundle export saga.

Owns the phase sequencing across SHORT units of work — authorize + write the
initial ``QUEUED`` state doc + enqueue in one short scope, read status in
another. A pooled DB connection is never held across archive assembly or the
object-storage upload (those live in the worker job and the streaming response
body respectively). Mirrors the ``FunctionUseCases`` phase-split discipline.

Download is URL-based: the job mints a signed, authenticated download URL onto
the state; the download endpoint verifies the token (no pod scope) and streams
the staged archive. ``open_download_by_token`` holds no DB at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleStagingMissingError,
)
from app.modules.pod_bundle.domain.state import ExportState, ExportStatus
from app.modules.pod_bundle.infrastructure.download_url import verify_download_token
from app.modules.pod_bundle.infrastructure.rate_limiter import (
    BundleRateLimiter,
    get_bundle_rate_limiter,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

EXPORT_JOB_NAME = "export_pod_bundle"


def export_job_id(export_id: UUID) -> str:
    return f"pod-export:{export_id}"


class ExportUseCases:
    """Owns the export saga. Built from a uow_factory (+ optional injected
    state store / staging for tests)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        state_store: PodBundleStateStore | None = None,
        staging: BundleStagingStorage | None = None,
        job_queue=None,
        rate_limiter: BundleRateLimiter | None = None,
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._staging = staging or BundleStagingStorage()
        self._job_queue = job_queue or get_streaq_job_queue()
        self._rate_limiter = rate_limiter or get_bundle_rate_limiter()

    async def start_export(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        with_data: bool,
        include: list[str] | None,
        data_tables: list[str] | None = None,
        with_files: bool = False,
        ttl_seconds: int | None = None,
    ) -> ExportState:
        """Authorize POD_READ, persist a ``QUEUED`` state doc, and enqueue the
        export job. ``ttl_seconds`` (the download URL's validity + archive
        retention) is clamped to the configured maximum."""
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(Permissions.POD_READ)

        # Abuse guard: count this export against the user's daily cap only after
        # they've proven POD_READ, so an unauthorized probe never burns quota.
        await self._rate_limiter.check_and_increment(
            user_id=user_id,
            operation="export",
            limit=pod_bundle_settings.pod_bundle_daily_export_limit,
        )

        resolved_ttl = _clamp_ttl(ttl_seconds)
        export_id = uuid4()
        state = ExportState(
            export_id=export_id,
            pod_id=pod_id,
            user_id=user_id,
            status=ExportStatus.QUEUED,
            with_data=with_data,
            data_tables=data_tables,
            with_files=with_files,
            include=include,
            ttl_seconds=resolved_ttl,
        )
        await self._state_store.save_export(state)

        job = await self._job_queue.enqueue(
            EXPORT_JOB_NAME,
            context={
                "export_id": str(export_id),
                "pod_id": str(pod_id),
                "user_id": str(user_id),
            },
            _job_id=export_job_id(export_id),
        )
        if job is None:
            raise BundleJobConflictError("An identical export is already in progress.")
        return state

    async def get_export(
        self, *, pod_id: UUID, export_id: UUID, user_id: UUID
    ) -> ExportState:
        """Authorize POD_READ and return the (pure-Redis) state doc. Raises
        :class:`BundleJobExpiredError` when the doc is gone (TTL/never existed)."""
        await self._authorize(pod_id=pod_id, user_id=user_id)
        state = await self._state_store.get_export(export_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        return state

    async def open_download_by_token(
        self, token: str
    ) -> tuple[str, AsyncIterator[bytes]]:
        """Verify a signed download token and stream the staged archive.

        Authorization is the token itself (verified here) plus the endpoint's
        ``CurrentUser`` gate — no pod scope, no DB. Raises
        :class:`BundleJobExpiredError` (410) for a bad/expired token and
        :class:`BundleStagingMissingError` (410) if the archive was swept.
        """
        kind, job_id = verify_download_token(token)
        iterator = await self._staging.iter_archive(kind, job_id)
        if iterator is None:
            raise BundleStagingMissingError()
        filename = f"{job_id}.zip"
        if kind == "pod-exports":
            state = await self._state_store.get_export(job_id)
            if state is not None and state.bundle_filename:
                filename = state.bundle_filename
        return filename, iterator

    async def _authorize(self, *, pod_id: UUID, user_id: UUID) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(Permissions.POD_READ)


def _clamp_ttl(ttl_seconds: int | None) -> int:
    default = pod_bundle_settings.pod_bundle_export_url_ttl_seconds
    ceiling = pod_bundle_settings.pod_bundle_export_url_max_ttl_seconds
    if ttl_seconds is None or ttl_seconds <= 0:
        return default
    return min(ttl_seconds, ceiling)
