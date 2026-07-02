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
    BundleConfirmationRequiredError,
    BundleInvalidError,
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleTooLargeError,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PLAN_JOB_NAME = "plan_pod_import"
GITHUB_JOB_NAME = "import_pod_github"
APPLY_JOB_NAME = "apply_pod_import"


def import_apply_job_id(import_id: UUID) -> str:
    return f"pod-import:{import_id}"

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

    async def start_github_import(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        repo_url: str | None,
        owner: str | None,
        repo: str | None,
        ref: str | None,
    ) -> ImportState:
        """Authorize POD_UPDATE, validate the repo reference, and enqueue the
        GitHub import job (fetch → stage → plan under one import_id)."""
        from app.modules.pod_bundle.infrastructure.github_fetcher import parse_repo_ref

        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)
        owner, repo = parse_repo_ref(repo_url=repo_url, owner=owner, repo=repo)

        import_id = uuid4()
        state = ImportState(
            import_id=import_id,
            pod_id=pod_id,
            user_id=user_id,
            status=ImportStatus.QUEUED,
            source=BundleSource(
                kind="github",
                repo_url=repo_url or f"https://github.com/{owner}/{repo}",
                ref=ref,
            ),
        )
        await self._state_store.save_import(state)
        job = await self._job_queue.enqueue(
            GITHUB_JOB_NAME,
            context={
                "import_id": str(import_id),
                "pod_id": str(pod_id),
                "user_id": str(user_id),
                "owner": owner,
                "repo": repo,
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

    async def apply_import(
        self,
        *,
        pod_id: UUID,
        import_id: UUID,
        user_id: UUID,
        variables: dict[str, str] | None = None,
        confirm_destructive: bool = False,
    ) -> ImportState:
        """Validate the plan is ready + confirmed, persist the resolved variables,
        and enqueue the apply job (dedup id doubles as the concurrency guard)."""
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        if state.plan is None or state.status not in (
            ImportStatus.AWAITING_CONFIRMATION,
            ImportStatus.FAILED,
        ):
            raise BundleJobConflictError(
                f"Import cannot be applied from status {state.status.value}."
            )
        if state.plan.has_destructive_steps and not confirm_destructive:
            raise BundleConfirmationRequiredError(
                "This import would drop or alter table columns. Re-submit with "
                "confirm_destructive=true to proceed.",
                details={"warnings": state.plan.warnings},
            )
        missing = [
            v.name
            for v in state.plan.variables
            if v.required and not (variables or {}).get(v.name)
        ]
        if missing:
            raise BundleConfirmationRequiredError(
                "Required variables are missing.", details={"missing": missing}
            )

        # Reset any FAILED step back to PENDING so a re-apply retries it; DONE
        # steps stay DONE (idempotent resume).
        for step in state.plan.steps:
            if step.status == StepStatus.FAILED:
                step.status = StepStatus.PENDING
                step.error = None
        state.variables_provided = dict(variables or {})
        state.confirm_destructive = confirm_destructive
        state.status = ImportStatus.APPLYING
        await self._state_store.save_import(state)

        job = await self._job_queue.enqueue(
            APPLY_JOB_NAME,
            context={
                "import_id": str(import_id),
                "pod_id": str(pod_id),
                "user_id": str(user_id),
            },
            _job_id=import_apply_job_id(import_id),
        )
        if job is None:
            raise BundleJobConflictError("This import is already being applied.")
        return state

    async def replan_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        """Re-run planning against the still-staged bundle (the resume path after
        Redis state drifts or the pod changed). 410 if the archive was swept."""
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        state.status = ImportStatus.QUEUED
        state.plan = None
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

    async def cancel_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> None:
        """Abort any running plan/apply job and delete the state + staged archive."""
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        for job_id in (import_plan_job_id(import_id), import_apply_job_id(import_id)):
            try:
                # Bounded: abort blocks until the task acknowledges, which never
                # happens for an already-finished/never-enqueued job. Cancel's
                # real guarantee is deleting the state + staged archive below, so
                # a best-effort short-timeout abort is enough.
                await self._job_queue.abort(job_id, timeout_seconds=2.0)
            except Exception:  # noqa: BLE001 - the job may already be gone
                pass
        try:
            await self._staging.delete_archive("pod-imports", import_id)
        except Exception:  # noqa: BLE001
            pass
        await self._state_store.delete_import(import_id)

    async def _authorize(self, *, pod_id: UUID, user_id: UUID, action) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
