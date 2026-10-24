"""Application/use-case layer for the pod-bundle import saga.

This slice owns upload → plan → status. Each public method opens only SHORT
units of work (authorize + stage + enqueue; or a pure Redis read) and never
holds a pooled connection across the archive upload or the planning job.

Single-writer contract: starting an import writes the initial ``QUEUED`` state
and its planning work item (dedup ``pod-import-plan:{import_id}``) in one
transaction; from that point the planning job is the only writer of the state
row, until the person applies or cancels.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from obstore.exceptions import BaseError as ObjectStoreError

from app.core.authorization.permissions import Permissions
from app.core.api.uploads import upload_source_size
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import (
    BundleConfirmationRequiredError,
    BundleInvalidError,
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleJobNotFoundError,
    BundleTooLargeError,
)
from app.modules.pod_bundle.domain.connector_plan import (
    ConnectorChoice,
    resolve_connector_choices,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportState,
    ImportStatus,
    StepStatus,
)
from app.modules.pod_bundle.events.work import (
    APPLY,
    IMPORT_GITHUB,
    IMPORT_URL,
    PLAN,
    PLANNING_KINDS,
    BundleJobQueue,
    GithubImportJob,
    ImportJob,
    UrlImportJob,
    apply_key,
    plan_key,
)
from app.modules.pod_bundle.infrastructure.rate_limiter import (
    BundleRateLimiter,
    get_bundle_rate_limiter,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.application.template_imports import stage_template
from app.core.concurrency.offload import run_blocking
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PLAN_JOB_NAME = PLAN
GITHUB_JOB_NAME = IMPORT_GITHUB
URL_JOB_NAME = IMPORT_URL
APPLY_JOB_NAME = APPLY


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


def _resolve_connector_choices(
    state: ImportState, requested: dict[str, ConnectorChoice] | None
) -> dict[str, ConnectorChoice]:
    """The importer's connector choices made whole, or why the apply is held.

    Checked here, before anything is queued, so a connector that cannot be
    declared stops the import while the pod is still untouched -- rather than
    failing at its step after nothing, or worse after half the pod, landed."""
    if state.plan is None:
        return {}
    resolved, blocking = resolve_connector_choices(
        state.plan.connectors, requested or {}
    )
    if blocking:
        raise BundleConfirmationRequiredError(
            "Some of this bundle's connectors cannot be declared yet: "
            + "; ".join(f"'{b.slug}': {b.reason}" for b in blocking)
            + ".",
            details={"blocking": [b.model_dump() for b in blocking]},
        )
    return resolved


logger = get_logger(__name__)


def import_apply_job_id(import_id: UUID) -> str:
    return apply_key(import_id)


# Local file signatures we accept as bundle archives (zip magic bytes). The deep
# structural validation is the plan job's responsibility; this is a cheap gate.
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _read_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as source:
        return source.read(size)


def import_plan_job_id(import_id: UUID) -> str:
    return plan_key(import_id)


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
        job_queue: BundleJobQueue | None = None,
        rate_limiter: BundleRateLimiter | None = None,
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._staging = staging or BundleStagingStorage()
        self._job_queue = job_queue or BundleJobQueue()
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
            await run_blocking(_read_prefix, data, 4)
            if isinstance(data, Path)
            else data[:4]
        )
        if not prefix.startswith(_ZIP_MAGIC):
            raise BundleInvalidError("The uploaded file is not a valid .zip bundle.")

        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )

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
        template: str | None = None,
    ) -> ImportState:
        """Single URL-based import entry point.

        ``URL``: verify the lemma signed download token (410 on bad/expired) and
        queue ``pod_bundle.import_url`` with the resolved source object — the
        worker reads it straight from object storage (no server-side fetch, no
        SSRF). ``GITHUB``: parse the repo reference and queue
        ``pod_bundle.import_github``. ``TEMPLATE``: pack the named template that
        ships with the backend (404 for an unknown name), stage it, and queue
        the plan straight away. The request carries no bytes in any case.
        """
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )

        # Abuse guard: count this import against the user's daily cap (separate
        # bucket from exports) once POD_UPDATE is authorized. Staging an upload
        # is not counted — only starting the plan/apply pipeline is — and nor
        # is a template: what it carries ships with this server, so its cost is
        # small and known, and the allowance is for bundles people author.
        if kind is not BundleSourceKind.TEMPLATE:
            await self._rate_limiter.check_and_increment(
                user_id=user_id,
                operation="import",
                limit=pod_bundle_settings.pod_bundle_daily_import_limit,
            )

        import_id = uuid4()

        if kind == BundleSourceKind.TEMPLATE:
            if not template:
                raise BundleInvalidError(
                    "A template name is required for kind=TEMPLATE."
                )
            staged = await stage_template(self._staging, template, import_id)
            state = staged.import_state(pod_id=pod_id, user_id=user_id)
            # Already staged, so straight to planning: the same job (and dedup
            # key) a replan uses, with nothing left to fetch.
            kind_name: str = PLAN
            job: ImportJob = ImportJob(
                import_id=import_id, pod_id=pod_id, user_id=user_id
            )
        elif kind == BundleSourceKind.URL:
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
            kind_name = IMPORT_URL
            job = UrlImportJob(
                import_id=import_id,
                pod_id=pod_id,
                user_id=user_id,
                source_kind=src_kind,
                source_id=src_id,
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
            kind_name = IMPORT_GITHUB
            job = GithubImportJob(
                import_id=import_id,
                pod_id=pod_id,
                user_id=user_id,
                owner=owner,
                repo=repo,
            )

        async with self._uow_factory() as uow:
            await self._state_store.stage_import(uow, state)
            if not await self._job_queue.queue_job(
                uow, kind_name, job, pod_id=pod_id, dedup_key=plan_key(import_id)
            ):
                raise BundleJobConflictError("This import is already being planned.")
            await uow.commit()
        return state

    async def get_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_READ
        )
        return await self._starters_import(
            pod_id=pod_id, import_id=import_id, user_id=user_id
        )

    async def apply_import(
        self,
        *,
        pod_id: UUID,
        import_id: UUID,
        user_id: UUID,
        variables: dict[str, str] | None = None,
        confirm_destructive: bool = False,
        connectors: dict[str, ConnectorChoice] | None = None,
    ) -> ImportState:
        """Validate the plan is ready + confirmed, persist the resolved variables
        and connector choices, and queue the apply job with them (dedup key
        doubles as the guard)."""
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        state = await self._starters_import(
            pod_id=pod_id, import_id=import_id, user_id=user_id
        )
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
        state.connector_choices = _resolve_connector_choices(state, connectors)
        # Grants the importer could not make themselves: refused here, with the
        # pod untouched, rather than by the last step of the apply. Imported
        # here: only an apply needs it, not every process serving the routes.
        from app.modules.pod_bundle.infrastructure.grant_planning import (
            refuse_unconferrable_grants,
        )

        refuse_unconferrable_grants(state.plan)
        await self._check_accounts(state, user_id=user_id, variables=variables)

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
        async with self._uow_factory() as uow:
            if retrying_failed_job:
                await self._state_store.stage_import(uow, state, reopen=True)
            else:
                await self._state_store.stage_import(uow, state)
            if not await self._job_queue.queue_job(
                uow,
                APPLY,
                ImportJob(import_id=import_id, pod_id=pod_id, user_id=user_id),
                pod_id=pod_id,
                dedup_key=apply_key(import_id),
            ):
                raise BundleJobConflictError("This import is already being applied.")
            await uow.commit()
        return state

    async def replan_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        """Re-run planning against the still-staged bundle (the resume path after
        Redis state drifts or the pod changed). 410 if the archive was swept."""
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        state = await self._starters_import(
            pod_id=pod_id, import_id=import_id, user_id=user_id
        )
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
        async with self._uow_factory() as uow:
            if retrying_failed_job:
                await self._state_store.stage_import(uow, state, reopen=True)
            else:
                await self._state_store.stage_import(uow, state)
            if not await self._job_queue.queue_job(
                uow,
                PLAN,
                ImportJob(import_id=import_id, pod_id=pod_id, user_id=user_id),
                pod_id=pod_id,
                dedup_key=plan_key(import_id),
            ):
                raise BundleJobConflictError("This import is already being planned.")
            await uow.commit()
        return state

    async def cancel_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        """Persist a cancellation tombstone and request cooperative worker stop.

        A job nothing is running ends here, in the same transaction: one
        awaiting confirmation (no item), or one whose item was still queued
        (the item is dropped). A running one sees the tombstone at its next
        step boundary and finalizes itself.
        """
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        state = await self._starters_import(
            pod_id=pod_id, import_id=import_id, user_id=user_id
        )
        if state.is_terminal:
            return state
        previous_status = state.status
        state.status = ImportStatus.CANCELLING
        state.cancel_requested_at = datetime.now(timezone.utc)
        finalized = False
        async with self._uow_factory() as uow:
            await self._state_store.stage_import(uow, state)
            # Keep the accepted response stable even when an idle cancellation
            # reaches its terminal state before this request returns.
            accepted = state.model_copy(deep=True)
            idle = previous_status == ImportStatus.AWAITING_CONFIRMATION
            if not idle:
                idle = await self._job_queue.drop_queued(
                    uow,
                    (*PLANNING_KINDS, APPLY),
                    pod_id=pod_id,
                    dedup_key=(
                        apply_key(import_id)
                        if previous_status == ImportStatus.APPLYING
                        else plan_key(import_id)
                    ),
                )
            if idle:
                state.status = (
                    ImportStatus.PARTIALLY_CANCELLED
                    if state.committed_steps
                    else ImportStatus.CANCELLED
                )
                if state.status is ImportStatus.PARTIALLY_CANCELLED:
                    # As the job says when it stops itself mid-apply: the
                    # steps that landed were not undone.
                    applied = len(state.committed_steps)
                    total = len(state.plan.steps) if state.plan else 0
                    state.error = (
                        f"Cancelled after {applied} of {total} steps had been "
                        "applied to this pod. Those changes were not undone; "
                        "import the bundle again to finish, or remove what it "
                        "created."
                    )
                state.current_step = None
                state.completed_at = datetime.now(timezone.utc)
                await self._state_store.stage_import(uow, state)
                finalized = True
            await uow.commit()
        if finalized:
            try:
                await self._staging.delete_archive("pod-imports", import_id)
            except ObjectStoreError:
                logger.debug(
                    "pod_bundle.import_use_cases.clean_staging_idle_cancelled_import.diagnostic",
                    import_id=str(import_id),
                )

        # Active workers observe the durable tombstone before/after external I/O
        # and before commit. Force-aborting them could bypass that finalization
        # path and strand the job in CANCELLING.
        return accepted

    async def _check_accounts(
        self,
        state: ImportState,
        *,
        user_id: UUID,
        variables: dict[str, str] | None,
    ) -> None:
        """The accounts this apply would use, checked while the pod is still
        untouched (``infrastructure/account_checks``): a narrow share is
        refused, and a schedule naming an account the importer cannot use is
        planned as skipped now that its import variable is known."""
        from app.modules.pod_bundle.infrastructure.account_checks import (
            ServiceAccountUsability,
            refuse_narrow_shares,
            skip_unusable_accounts,
            steps_naming_an_account,
        )

        assert state.plan is not None, "checked by the caller"
        shares = {
            slug: choice.share_account_id
            for slug, choice in state.connector_choices.items()
            if choice.share_account_id is not None
        }
        if not shares and not steps_naming_an_account(state.plan.steps):
            return
        replacements = {
            v.name: v.default for v in state.plan.variables if v.default is not None
        }
        replacements.update(variables or {})
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=state.pod_id
            )
            async with context_scope(ctx):
                await refuse_narrow_shares(uow, pod_id=state.pod_id, shares=shares)
                await skip_unusable_accounts(
                    state.plan.steps,
                    state.warnings,
                    replacements=replacements,
                    check=ServiceAccountUsability(
                        uow=uow, ctx=ctx, pod_id=state.pod_id
                    ),
                )

    async def _starters_import(
        self, *, pod_id: UUID, import_id: UUID, user_id: UUID
    ) -> ImportState:
        """The import, when ``user_id`` started it; 410 once it expired.

        Bound to its starter: the plan was computed with their authority and
        lists their own accounts, so another editor of the pod who learns the
        id may neither read it nor apply it as though it were theirs.
        """
        state = await self._state_store.get_import(import_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        if state.user_id != user_id:
            raise BundleJobNotFoundError()
        return state

    async def _authorize(self, *, pod_id: UUID, user_id: UUID, action) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
