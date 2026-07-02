"""Application/use-case layer for the pod-bundle export saga.

Owns the phase sequencing across SHORT units of work — authorize + write the
initial ``QUEUED`` state doc + enqueue in one short scope, read status in
another, stream the download in a third. A pooled DB connection is never held
across the archive assembly or the object-storage upload (those live in the
worker job and the streaming response body respectively). Mirrors the
``FunctionUseCases`` phase-split discipline.

Single-writer contract: this class writes the initial ``ExportState`` and
enqueues with the dedup job id ``pod-export:{export_id}``; from that point the
worker is the only writer of the state doc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleStagingMissingError,
)
from app.modules.pod_bundle.domain.state import ExportState, ExportStatus
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
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._staging = staging or BundleStagingStorage()
        self._job_queue = job_queue or get_streaq_job_queue()

    async def start_export(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        with_data: bool,
        include: list[str] | None,
    ) -> ExportState:
        """Authorize POD_READ, persist a ``QUEUED`` state doc, and enqueue the
        export job. Returns the state (the API surfaces ``export_id`` + status).
        """
        # Short UoW: authorize as the requesting user, then release the
        # connection — the job does all the heavy reads later.
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(Permissions.POD_READ)

        export_id = uuid4()
        state = ExportState(
            export_id=export_id,
            pod_id=pod_id,
            user_id=user_id,
            status=ExportStatus.QUEUED,
            with_data=with_data,
            include=include,
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
            # A fresh export_id can never collide, but keep the contract honest:
            # a duplicate dedup id means an identical export is already queued.
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

    async def open_download(
        self, *, pod_id: UUID, export_id: UUID, user_id: UUID
    ) -> tuple[str, AsyncIterator[bytes]]:
        """Authorize POD_READ, ensure the export is ``READY``, and return
        ``(bundle_filename, chunk_iterator)`` streaming the staged archive.

        The auth + state read happen in a short scope; the returned iterator
        streams from object storage with no pooled connection held.
        """
        await self._authorize(pod_id=pod_id, user_id=user_id)
        state = await self._state_store.get_export(export_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        if state.status != ExportStatus.READY:
            raise BundleJobConflictError(
                f"Export is not ready to download (status: {state.status.value})."
            )

        iterator = await self._staging.iter_archive("pod-exports", export_id)
        if iterator is None:
            # State says READY but the archive was swept — surface the staging-gone
            # condition so the caller re-runs the export.
            raise BundleStagingMissingError()
        filename = state.bundle_filename or f"{export_id}.zip"
        return filename, iterator

    async def _authorize(self, *, pod_id: UUID, user_id: UUID) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(Permissions.POD_READ)
