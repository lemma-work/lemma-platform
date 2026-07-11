"""Application/use-case layer for the pod-bundle import saga.

This slice owns upload → plan → status. Each public method opens only SHORT
units of work (authorize + stage + enqueue; or a pure Redis read) and never
holds a pooled connection across the archive upload or the planning job.

Single-writer contract: ``start_upload_import`` writes the initial ``QUEUED``
state and enqueues with the dedup job id ``pod-import-plan:{import_id}``; from
that point the ``plan_pod_import`` worker is the only writer of the state doc.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from obstore.exceptions import BaseError as ObjectStoreError

from app.core.authorization.permissions import Permissions
from app.core.api.uploads import upload_source_size
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.core.log.log import get_logger
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
    BundleSourceKind,
    ImportState,
    ImportStatus,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.rate_limiter import (
    BundleRateLimiter,
    get_bundle_rate_limiter,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PLAN_JOB_NAME = "plan_pod_import"
GITHUB_JOB_NAME = "import_pod_github"
URL_JOB_NAME = "import_pod_url"
APPLY_JOB_NAME = "apply_pod_import"


def _require_import_variables(
    state: ImportState,
    variables: dict[str, str] | None,
) -> None:
    if state.plan is None:
        return
    missing = [
        item.name
        for item in state.plan.variables
        if item.required and not (variables or {}).get(item.name)
    ]
    if missing:
        raise BundleConfirmationRequiredError(
            "Required variables are missing.", details={"missing": missing}
        )
logger = get_logger(__name__)


def import_apply_job_id(import_id: UUID) -> str:
    return f"pod-import:{import_id}"

# Local file signatures we accept as bundle archives (zip magic bytes). The deep
# structural validation is the plan job's responsibility; this is a cheap gate.
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _read_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as source:
        return source.read(size)


def import_plan_job_id(import_id: UUID) -> str:
    return f"pod-import-plan:{import_id}"


def _resolve_lemma_url(url: str) -> tuple[str, UUID]:
    """Extract + verify the signed token from a lemma download URL, returning the
    staged object's ``(kind, id)``. Raises :class:`BundleInvalidError` (422) if
    the URL isn't a lemma download link, or :class:`BundleJobExpiredError` (410)
    if the token is bad/expired."""
    from urllib.parse import parse_qs, urlparse

    from app.modules.pod_bundle.infrastructure.download_url import (
        DOWNLOAD_PATH,
        verify_download_token,
    )

    parsed = urlparse(url)
    if not parsed.path.endswith(DOWNLOAD_PATH):
        raise BundleInvalidError(
            "kind=URL requires a lemma bundle download URL "
            "(export it or upload a .zip first)."
        )
    token = (parse_qs(parsed.query).get("token") or [None])[0]
    if not token:
        raise BundleInvalidError("The download URL is missing its token.")
    kind, job_id = verify_download_token(token)
    return kind, job_id


class ImportUseCases:
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

    async def stage_upload(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        filename: str | None,
        data: bytes | Path,
    ) -> tuple[str, datetime]:
        """Stage a locally-uploaded ``.zip`` and return a signed lemma download
        URL to feed the URL-based import. The only multipart entry point; carries
        no orchestration. Raises 413 over the size cap / 422 for a non-zip."""
        if upload_source_size(data) > pod_bundle_settings.pod_bundle_max_archive_bytes:
            raise BundleTooLargeError(
                "The uploaded bundle exceeds the maximum allowed size."
            )
        prefix = (
            await asyncio.to_thread(_read_prefix, data, 4)
            if isinstance(data, Path)
            else data[:4]
        )
        if not prefix.startswith(_ZIP_MAGIC):
            raise BundleInvalidError("The uploaded file is not a valid .zip bundle.")

        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)

        from datetime import datetime, timedelta, timezone

        from app.modules.pod_bundle.infrastructure.download_url import (
            build_download_url,
        )

        upload_id = uuid4()
        await self._staging.put_archive("pod-imports", upload_id, data)
        ttl = pod_bundle_settings.pod_bundle_state_ttl_seconds
        url = build_download_url(kind="pod-imports", job_id=upload_id, ttl_seconds=ttl)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return url, expires_at

    async def start_import(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        kind: BundleSourceKind,
        url: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        ref: str | None = None,
        account_id: UUID | None = None,
    ) -> ImportState:
        """Single URL-based import entry point.

        ``URL``: verify the lemma signed download token (410 on bad/expired) and
        enqueue ``import_pod_url`` with the resolved source object — the worker
        reads it straight from object storage (no server-side fetch, no SSRF).
        ``GITHUB``: parse the repo reference and enqueue ``import_pod_github``.
        The request carries no bytes either way.
        """
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)

        # Abuse guard: count this import against the user's daily cap (separate
        # bucket from exports) once POD_UPDATE is authorized. Staging an upload
        # is not counted — only starting the plan/apply pipeline is.
        await self._rate_limiter.check_and_increment(
            user_id=user_id,
            operation="import",
            limit=pod_bundle_settings.pod_bundle_daily_import_limit,
        )

        import_id = uuid4()

        if kind == BundleSourceKind.URL:
            if not url:
                raise BundleInvalidError("A url is required for kind=URL.")
            src_kind, src_id = _resolve_lemma_url(url)
            state = ImportState(
                import_id=import_id,
                pod_id=pod_id,
                user_id=user_id,
                status=ImportStatus.QUEUED,
                source=BundleSource(kind=BundleSourceKind.URL, url=url),
            )
            await self._state_store.save_import(state)
            job = await self._job_queue.enqueue(
                URL_JOB_NAME,
                context={
                    "import_id": str(import_id),
                    "pod_id": str(pod_id),
                    "user_id": str(user_id),
                    "source_kind": src_kind,
                    "source_id": str(src_id),
                },
                _job_id=import_plan_job_id(import_id),
            )
        else:  # GITHUB
            from app.modules.pod_bundle.infrastructure.github_fetcher import (
                parse_repo_ref,
            )

            owner, repo = parse_repo_ref(repo_url=url, owner=owner, repo=repo)
            state = ImportState(
                import_id=import_id,
                pod_id=pod_id,
                user_id=user_id,
                status=ImportStatus.QUEUED,
                source=BundleSource(
                    kind=BundleSourceKind.GITHUB,
                    repo_url=url or f"https://github.com/{owner}/{repo}",
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
                    "account_id": str(account_id) if account_id else None,
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
        _require_import_variables(state, variables)

        retrying_failed_job = state.status is ImportStatus.FAILED
        # Reset any FAILED step back to PENDING so a re-apply retries it; DONE
        # steps stay DONE (idempotent resume).
        for step in state.plan.steps:
            if step.status == StepStatus.FAILED:
                step.status = StepStatus.PENDING
                step.error = None
        state.variables_provided = dict(variables or {})
        state.confirm_destructive = confirm_destructive
        state.status = ImportStatus.APPLYING
        state.error = None
        state.error_type = None
        state.error_code = None
        state.completed_at = None
        if retrying_failed_job:
            state.attempt += 1
            await self._state_store.reopen_import(state)
        else:
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
        if state.status not in {
            ImportStatus.AWAITING_CONFIRMATION,
            ImportStatus.FAILED,
        }:
            raise BundleJobConflictError(
                f"Import cannot be replanned from status {state.status.value}."
            )
        retrying_failed_job = state.status is ImportStatus.FAILED
        state.status = ImportStatus.QUEUED
        state.plan = None
        state.error = None
        state.error_type = None
        state.error_code = None
        state.completed_at = None
        if retrying_failed_job:
            state.attempt += 1
            await self._state_store.reopen_import(state)
        else:
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
    ) -> ImportState:
        """Persist a cancellation tombstone and request cooperative worker stop."""
        await self._authorize(pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE)
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        if state.is_terminal:
            return state
        previous_status = state.status
        state.status = ImportStatus.CANCELLING
        state.cancel_requested_at = datetime.now(timezone.utc)
        await self._state_store.save_import(state)

        # Keep the accepted response stable even when an idle cancellation can
        # reach its terminal state before this request returns.
        accepted = state.model_copy(deep=True)
        if previous_status == ImportStatus.AWAITING_CONFIRMATION:
            state.status = ImportStatus.CANCELLED
            state.current_step = None
            state.completed_at = datetime.now(timezone.utc)
            await self._state_store.save_import(state)
            try:
                await self._staging.delete_archive("pod-imports", import_id)
            except ObjectStoreError:
                logger.warning(
                    "Failed to clean staging for idle cancelled import",
                    import_id=str(import_id),
                )

        # Active workers observe the durable tombstone before/after external I/O
        # and before commit. Force-aborting them could bypass that finalization
        # path and strand the job in CANCELLING.
        return accepted

    async def _authorize(self, *, pod_id: UUID, user_id: UUID, action) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
