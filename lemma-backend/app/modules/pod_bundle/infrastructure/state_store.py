"""PostgreSQL-authoritative state store for every pod-bundle job kind."""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar, cast
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import BundleStateConflictError
from app.modules.pod_bundle.domain.state import (
    BundleJobKind,
    ExportState,
    ExportStatus,
    ImportState,
    ImportStatus,
    PublishState,
    PublishStatus,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.models import (
    PodBundleJob,
    PodBundleJobStep,
)

StateT = TypeVar("StateT", ImportState, ExportState, PublishState)
BundleState = ImportState | ExportState | PublishState

_KEY_PREFIX = "pod-bundle"
_ACTIVE_STATUSES = (
    ImportStatus.QUEUED.value,
    ImportStatus.FETCHING.value,
    ImportStatus.PLANNING.value,
    ImportStatus.APPLYING.value,
    ImportStatus.CANCELLING.value,
    ExportStatus.QUEUED.value,
    ExportStatus.EXPORTING.value,
    PublishStatus.PUBLISHING.value,
)
_IMMUTABLE_STATUSES = {
    BundleJobKind.IMPORT: {
        ImportStatus.COMPLETED.value,
        ImportStatus.CANCELLED.value,
        ImportStatus.PARTIALLY_CANCELLED.value,
    },
    BundleJobKind.EXPORT: {ExportStatus.READY.value},
    BundleJobKind.PUBLISH: {PublishStatus.COMPLETED.value},
}
logger = get_logger(__name__)


def _import_checkpoints(state: ImportState) -> list[dict]:
    if state.plan is None:
        return []
    return [
        {
            "step_index": step.index,
            "phase": "APPLY",
            "kind": step.kind.value,
            "name": step.name,
            "status": step.status.value,
            "error": step.error,
            "committed_at": (
                state.updated_at
                if step.status in {StepStatus.DONE, StepStatus.SKIPPED}
                else None
            ),
        }
        for step in state.plan.steps
    ]


def _export_checkpoints(state: ExportState) -> list[dict]:
    status = {
        ExportStatus.QUEUED: StepStatus.PENDING,
        ExportStatus.EXPORTING: StepStatus.RUNNING,
        ExportStatus.READY: StepStatus.DONE,
        ExportStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    return [
        {
            "step_index": 0,
            "phase": "EXPORT",
            "kind": "ARCHIVE",
            "name": "Export pod bundle",
            "status": status.value,
            "error": state.error,
            "committed_at": (
                state.completed_at if status is StepStatus.DONE else None
            ),
        }
    ]


def _publish_checkpoints(state: PublishState) -> list[dict]:
    export_status = {
        PublishStatus.QUEUED: StepStatus.PENDING,
        PublishStatus.EXPORTING: StepStatus.RUNNING,
        PublishStatus.PUBLISHING: StepStatus.DONE,
        PublishStatus.COMPLETED: StepStatus.DONE,
        PublishStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    publish_status = {
        PublishStatus.QUEUED: StepStatus.PENDING,
        PublishStatus.EXPORTING: StepStatus.PENDING,
        PublishStatus.PUBLISHING: StepStatus.RUNNING,
        PublishStatus.COMPLETED: StepStatus.DONE,
        PublishStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    checkpoints = [
        {
            "step_index": 0,
            "phase": "EXPORT",
            "kind": "ARCHIVE",
            "name": "Export pod bundle",
            "status": export_status.value,
            "error": state.error if export_status is StepStatus.FAILED else None,
            "committed_at": (
                state.updated_at if export_status is StepStatus.DONE else None
            ),
        },
        {
            "step_index": 1,
            "phase": "PUBLISH",
            "kind": "REPOSITORY",
            "name": state.repo_name,
            "status": publish_status.value,
            "error": state.error if publish_status is StepStatus.FAILED else None,
            "committed_at": (
                state.completed_at if publish_status is StepStatus.DONE else None
            ),
        },
    ]
    checkpoints.extend(
        {
            "step_index": index + 2,
            "phase": "PUBLISH",
            "kind": "FILE",
            "name": file.path,
            "status": file.status.value,
            "error": file.error,
            "committed_at": (
                state.updated_at if file.status is StepStatus.DONE else None
            ),
        }
        for index, file in enumerate(state.files)
    )
    return checkpoints


def _bundle_checkpoints(state: BundleState) -> list[dict]:
    if isinstance(state, ImportState):
        return _import_checkpoints(state)
    if isinstance(state, ExportState):
        return _export_checkpoints(state)
    return _publish_checkpoints(state)


class PodBundleStateStore:
    """Durable job snapshots with a best-effort Redis realtime mirror."""

    def __init__(self, cache: RedisJsonCache | None = None):
        # Injected caches keep unit tests hermetic. The process singleton uses
        # PostgreSQL as authority and Redis only as a mirror/legacy bridge.
        self._durable = cache is None
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_KEY_PREFIX,
            ttl_seconds=pod_bundle_settings.pod_bundle_state_ttl_seconds,
        )

    async def _get_cache(
        self,
        kind: BundleJobKind,
        job_id: UUID,
        model: type[StateT],
    ) -> StateT | None:
        raw = await self._cache.get_json(f"{kind.value.lower()}:{job_id}")
        return None if raw is None else model.model_validate(raw)

    async def _save_cache(
        self,
        kind: BundleJobKind,
        job_id: UUID,
        state: BundleState,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        await self._cache.set_json(
            f"{kind.value.lower()}:{job_id}",
            state.model_dump(mode="json"),
            ttl_seconds=ttl_seconds,
        )

    async def _get_state(
        self,
        kind: BundleJobKind,
        job_id: UUID,
        model_type: type[StateT],
    ) -> StateT | None:
        if not self._durable:
            return await self._get_cache(kind, job_id, model_type)

        async with async_session_maker() as session:
            model = await session.get(PodBundleJob, job_id)
        if model is not None:
            if model.job_kind != kind.value:
                return None
            state = model_type.model_validate(model.snapshot)
            state.version = model.version
            state.attempt = model.attempt
            state.heartbeat_at = model.heartbeat_at
            return state

        # Rolling-deployment bridge: lazily copy a legacy Redis-only snapshot.
        try:
            legacy = await self._get_cache(kind, job_id, model_type)
        except (RedisError, ValidationError):
            logger.warning(
                "Failed to inspect legacy pod bundle cache",
                job_kind=kind.value,
                job_id=str(job_id),
            )
            return None
        if legacy is None:
            return None
        persisted = await self._persist_state(kind, job_id, legacy)
        self._copy_store_fields(legacy, persisted)
        return legacy

    async def _save_state(
        self,
        kind: BundleJobKind,
        job_id: UUID,
        state: BundleState,
        *,
        ttl_seconds: int | None = None,
        allow_failed_reopen: bool = False,
    ) -> None:
        if not self._durable:
            state.touch()
            await self._save_cache(
                kind,
                job_id,
                state,
                ttl_seconds=ttl_seconds,
            )
            return

        persisted = await self._persist_state(
            kind,
            job_id,
            state,
            allow_failed_reopen=allow_failed_reopen,
        )
        self._copy_store_fields(state, persisted)
        try:
            await self._save_cache(
                kind,
                job_id,
                persisted,
                ttl_seconds=ttl_seconds,
            )
        except RedisError:
            logger.warning(
                "Failed to refresh pod bundle state cache",
                job_kind=kind.value,
                job_id=str(job_id),
                status=str(state.status),
            )

    async def _persist_state(
        self,
        kind: BundleJobKind,
        job_id: UUID,
        state: BundleState,
        *,
        allow_failed_reopen: bool = False,
    ) -> BundleState:
        candidate = state.model_copy(deep=True)
        async with async_session_maker() as session, session.begin():
            model = await session.scalar(
                select(PodBundleJob)
                .where(PodBundleJob.id == job_id)
                .with_for_update()
            )
            if model is None:
                if candidate.version != 0:
                    raise BundleStateConflictError(
                        "A new pod bundle job must start at version zero."
                    )
                candidate.version = 1
            else:
                if model.job_kind != kind.value or candidate.version != model.version:
                    raise BundleStateConflictError()
                current = self._parse_snapshot(kind, model.snapshot)
                current.version = model.version
                current.attempt = model.attempt
                self._validate_transition(
                    kind,
                    current,
                    candidate,
                    allow_failed_reopen=allow_failed_reopen,
                )
                candidate.version = model.version + 1

            candidate.touch()
            if not candidate.is_terminal:
                candidate.heartbeat_at = candidate.updated_at
            snapshot = candidate.model_dump(mode="json")
            if model is None:
                model = PodBundleJob(
                    id=job_id,
                    job_kind=kind.value,
                    pod_id=candidate.pod_id,
                    user_id=candidate.user_id,
                    status=candidate.status.value,
                    snapshot=snapshot,
                    version=candidate.version,
                    attempt=candidate.attempt,
                    heartbeat_at=candidate.heartbeat_at,
                    cancel_requested_at=getattr(
                        candidate, "cancel_requested_at", None
                    ),
                    current_step=getattr(candidate, "current_step", None),
                    committed_steps=getattr(candidate, "committed_steps", []),
                    error_type=candidate.error_type,
                    error_code=candidate.error_code,
                    error=candidate.error,
                    completed_at=candidate.completed_at,
                )
                session.add(model)
            else:
                model.status = candidate.status.value
                model.snapshot = snapshot
                model.version = candidate.version
                model.attempt = candidate.attempt
                model.heartbeat_at = candidate.heartbeat_at
                model.cancel_requested_at = getattr(
                    candidate, "cancel_requested_at", None
                )
                model.current_step = getattr(candidate, "current_step", None)
                model.committed_steps = getattr(candidate, "committed_steps", [])
                model.error_type = candidate.error_type
                model.error_code = candidate.error_code
                model.error = candidate.error
                model.completed_at = candidate.completed_at
            await self._sync_steps(session, job_id, candidate)
        return candidate

    @staticmethod
    def _validate_transition(
        kind: BundleJobKind,
        current: BundleState,
        candidate: BundleState,
        *,
        allow_failed_reopen: bool,
    ) -> None:
        old_status = current.status.value
        new_status = candidate.status.value
        if old_status in _IMMUTABLE_STATUSES[kind]:
            raise BundleStateConflictError("A completed pod bundle job is immutable.")
        if old_status == "FAILED" and new_status != "FAILED":
            if not allow_failed_reopen or candidate.attempt != current.attempt + 1:
                raise BundleStateConflictError(
                    "A failed pod bundle job requires an explicit retry."
                )
        elif candidate.attempt != current.attempt:
            raise BundleStateConflictError("A job attempt can change only on retry.")
        if old_status == ImportStatus.CANCELLING.value and new_status not in {
            ImportStatus.CANCELLING.value,
            ImportStatus.CANCELLED.value,
            ImportStatus.PARTIALLY_CANCELLED.value,
        }:
            raise BundleStateConflictError("Cancellation already won this job.")
        if (
            isinstance(current, ImportState)
            and isinstance(candidate, ImportState)
            and not set(current.committed_steps).issubset(candidate.committed_steps)
        ):
            raise BundleStateConflictError("Committed import steps cannot be removed.")

    async def _sync_steps(self, session, job_id: UUID, state: BundleState) -> None:
        checkpoints = self._checkpoints(state)
        existing = {
            row.step_index: row
            for row in (
                await session.scalars(
                    select(PodBundleJobStep).where(PodBundleJobStep.job_id == job_id)
                )
            ).all()
        }
        keep: set[int] = set()
        for checkpoint in checkpoints:
            index = cast(int, checkpoint["step_index"])
            keep.add(index)
            row = existing.get(index)
            if row is None:
                row = PodBundleJobStep(job_id=job_id, **checkpoint)
                session.add(row)
            else:
                for key, value in checkpoint.items():
                    setattr(row, key, value)
        if keep:
            await session.execute(
                delete(PodBundleJobStep).where(
                    PodBundleJobStep.job_id == job_id,
                    PodBundleJobStep.step_index.not_in(keep),
                )
            )
        else:
            await session.execute(
                delete(PodBundleJobStep).where(PodBundleJobStep.job_id == job_id)
            )

    @staticmethod
    def _checkpoints(state: BundleState) -> list[dict]:
        return _bundle_checkpoints(state)

    @staticmethod
    def _parse_snapshot(kind: BundleJobKind, snapshot: dict) -> BundleState:
        model = {
            BundleJobKind.IMPORT: ImportState,
            BundleJobKind.EXPORT: ExportState,
            BundleJobKind.PUBLISH: PublishState,
        }[kind]
        return model.model_validate(snapshot)

    @staticmethod
    def _copy_store_fields(target: BundleState, source: BundleState) -> None:
        target.version = source.version
        target.seq = source.seq
        target.updated_at = source.updated_at
        target.heartbeat_at = source.heartbeat_at

    async def get_import(self, import_id: UUID) -> ImportState | None:
        return await self._get_state(BundleJobKind.IMPORT, import_id, ImportState)

    async def save_import(self, state: ImportState) -> None:
        await self._save_state(BundleJobKind.IMPORT, state.import_id, state)

    async def reopen_import(self, state: ImportState) -> None:
        await self._save_state(
            BundleJobKind.IMPORT,
            state.import_id,
            state,
            allow_failed_reopen=True,
        )

    async def get_export(self, export_id: UUID) -> ExportState | None:
        return await self._get_state(BundleJobKind.EXPORT, export_id, ExportState)

    async def save_export(
        self,
        state: ExportState,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        await self._save_state(
            BundleJobKind.EXPORT,
            state.export_id,
            state,
            ttl_seconds=ttl_seconds,
        )

    async def reopen_export(self, state: ExportState) -> None:
        await self._save_state(
            BundleJobKind.EXPORT,
            state.export_id,
            state,
            allow_failed_reopen=True,
        )

    async def get_publish(self, publish_id: UUID) -> PublishState | None:
        return await self._get_state(BundleJobKind.PUBLISH, publish_id, PublishState)

    async def save_publish(self, state: PublishState) -> None:
        await self._save_state(BundleJobKind.PUBLISH, state.publish_id, state)

    async def reopen_publish(self, state: PublishState) -> None:
        await self._save_state(
            BundleJobKind.PUBLISH,
            state.publish_id,
            state,
            allow_failed_reopen=True,
        )

    async def recover_stale_jobs(
        self,
        *,
        cutoff: datetime,
        limit: int = 1_000,
    ) -> list[BundleState]:
        """Terminalize stale jobs from the database, independent of archives."""
        if not self._durable:
            return []
        recovered: list[BundleState] = []
        async with async_session_maker() as session, session.begin():
            rows = (
                await session.scalars(
                    select(PodBundleJob)
                    .where(
                        PodBundleJob.status.in_(_ACTIVE_STATUSES),
                        func.coalesce(
                            PodBundleJob.heartbeat_at,
                            PodBundleJob.updated_at,
                        )
                        < cutoff,
                    )
                    .order_by(PodBundleJob.heartbeat_at, PodBundleJob.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                kind = BundleJobKind(row.job_kind)
                state = self._parse_snapshot(kind, row.snapshot)
                state.version = row.version + 1
                state.error = "Interrupted by a worker restart; retry this job."
                state.error_type = "WorkerHeartbeatExpired"
                state.error_code = "POD_BUNDLE_WORKER_INTERRUPTED"
                state.completed_at = datetime.now(state.updated_at.tzinfo)
                if isinstance(state, ImportState) and (
                    state.status is ImportStatus.CANCELLING
                ):
                    state.status = (
                        ImportStatus.PARTIALLY_CANCELLED
                        if state.committed_steps
                        else ImportStatus.CANCELLED
                    )
                    state.error = None
                    state.error_type = None
                    state.error_code = None
                elif isinstance(state, ImportState):
                    state.status = ImportStatus.FAILED
                elif isinstance(state, ExportState):
                    state.status = ExportStatus.FAILED
                else:
                    state.status = PublishStatus.FAILED
                state.touch()
                row.status = state.status.value
                row.snapshot = state.model_dump(mode="json")
                row.version = state.version
                row.heartbeat_at = state.heartbeat_at
                row.error = state.error
                row.error_type = state.error_type
                row.error_code = state.error_code
                row.completed_at = state.completed_at
                await self._sync_steps(session, row.id, state)
                recovered.append(state)
        for state in recovered:
            kind, job_id = self._identity(state)
            try:
                await self._save_cache(kind, job_id, state)
            except RedisError:
                logger.warning(
                    "Failed to mirror recovered pod bundle job",
                    job_kind=kind.value,
                    job_id=str(job_id),
                )
        return recovered

    @staticmethod
    def _identity(state: BundleState) -> tuple[BundleJobKind, UUID]:
        if isinstance(state, ImportState):
            return BundleJobKind.IMPORT, state.import_id
        if isinstance(state, ExportState):
            return BundleJobKind.EXPORT, state.export_id
        return BundleJobKind.PUBLISH, state.publish_id

    async def delete_import(self, import_id: UUID) -> None:
        await self._cache.delete(f"import:{import_id}")

    async def delete_export(self, export_id: UUID) -> None:
        await self._cache.delete(f"export:{export_id}")

    async def delete_publish(self, publish_id: UUID) -> None:
        await self._cache.delete(f"publish:{publish_id}")

    async def close(self) -> None:
        await self._cache.close()


_state_store: PodBundleStateStore | None = None


def get_pod_bundle_state_store() -> PodBundleStateStore:
    global _state_store
    if _state_store is None:
        _state_store = PodBundleStateStore()
    return _state_store
