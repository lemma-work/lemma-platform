"""Application/use-case layer for the pod-bundle import saga.

This slice owns upload → plan → status. Each public method opens only SHORT
units of work (authorize + stage + enqueue; or a pure Redis read) and never
holds a pooled connection across the archive upload or the planning job.

Single-writer contract: ``start_upload_import`` writes the initial ``QUEUED``
state and enqueues with the dedup job id ``pod-import-plan:{import_id}``; from
that point the ``plan_pod_import`` worker is the only writer of the state doc.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleTooLargeError,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PLAN_JOB_NAME = "plan_pod_import"

# Local file signatures we accept as bundle archives (zip magic bytes). The deep
# structural validation is the plan job's responsibility; this is a cheap gate.
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def import_plan_job_id(import_id: UUID) -> str:
    return f"pod-import-plan:{import_id}"


class ImportUseCases:
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

    async def start_upload_import(
        self, *, pod_id: UUID, user_id: UUID, filename: str | None, data: bytes
    ) -> ImportState:
        """Authorize POD_UPDATE, stage the uploaded archive, and enqueue planning.

        Raises :class:`BundleTooLargeError` (413) over the size cap and
        :class:`BundleInvalidError` (422) for a non-zip payload — both before any
        object-storage write.
        """
        if len(data) > pod_bundle_settings.pod_bundle_max_archive_bytes:
            raise BundleTooLargeError(
                "The uploaded bundle exceeds the maximum allowed size."
            )
        if not data.startswith(_ZIP_MAGIC):
            raise BundleInvalidError("The uploaded file is not a valid .zip bundle.")

        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)

        import_id = uuid4()
        staging_key = await self._staging.put_archive("pod-imports", import_id, data)
        state = ImportState(
            import_id=import_id,
            pod_id=pod_id,
            user_id=user_id,
            status=ImportStatus.QUEUED,
            staging_key=staging_key,
            source=BundleSource(
                kind="upload",
                bundle_filename=filename,
                bundle_sha256=hashlib.sha256(data).hexdigest(),
            ),
        )
        await self._state_store.save_import(state)

        job = await self._job_queue.enqueue(
            PLAN_JOB_NAME,
            context={
                "import_id": str(import_id),
                "pod_id": str(pod_id),
                "user_id": str(user_id),
            },
            _job_id=import_plan_job_id(import_id),
        )
        if job is None:
            raise BundleJobConflictError("This import is already being planned.")
        return state

    async def get_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_READ)
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        return state

    async def _authorize(self, *, pod_id: UUID, user_id: UUID, action) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
