"""Streaq tasks for pod bundle jobs.

Imported for side effects by ``module.register_streaq`` at worker startup.
Tasks land slice by slice: export, plan, apply, GitHub import, publish, sweep.

Export job phases:
  (a) mark ``EXPORTING`` + publish status
  (b) one short UoW: build ctx, assemble the archive bytes via ``BundleExporter``
      (list+get reads inside the scope; progress writes bump Redis)
  (c) NO DB: upload the bytes to object storage
  (d) short state write ``READY`` (staging_key, bundle_filename, completed_at) +
      publish completed
On failure: mark ``FAILED`` + publish error. Domain/bundle errors are terminal
(swallowed after marking FAILED); infrastructure errors re-raise so streaq
retries — a retry re-plans a fresh export against current pod state, so it is
always safe.
"""

from __future__ import annotations

import tempfile
from uuid import UUID

from streaq import StreaqRetry

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.jobs.streaq_runtime import (
    Lane,
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)
from app.core.log.log import get_logger
from app.core.origin import OriginKind
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.events import analytics as bundle_analytics
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    BundleStagingMissingError,
    BundleStateConflictError,
)
from app.modules.pod_bundle.domain.state import (
    ExportState,
    ExportStatus,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.infrastructure.archive_offload import (
    extract_bundle_offloaded,
)
from app.modules.pod_bundle.infrastructure.exporter import BundleExporter
from app.modules.pod_bundle.infrastructure import github_fetcher
from app.modules.pod_bundle.infrastructure import publish_manifest
from app.modules.pod_bundle.infrastructure.realtime import (
    completed_payload,
    error_payload,
    progress_payload,
    publish_bundle_event,
    status_payload,
    step_payload,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)

logger = get_logger(__name__)


class _ImportCancellation(Exception):
    pass


def _github_import_fetcher(worker_ctx: AppWorkerContext, state: ImportState):
    runner = None
    if state.account_id is not None:
        runner = _github_import_operation_runner(worker_ctx=worker_ctx, state=state)
    elif (
        pod_bundle_settings.pod_bundle_github_token is None
        and "GitHub import used anonymous API access and may be rate-limited."
        not in state.warnings
    ):
        state.warnings.append(
            "GitHub import used anonymous API access and may be rate-limited."
        )
    return github_fetcher.GithubBundleFetcher(operation_runner=runner)


async def _cancellation_requested(store, import_id: UUID) -> ImportState | None:
    current = await store.get_import(import_id)
    if current is not None and current.status in {
        ImportStatus.CANCELLING,
        ImportStatus.CANCELLED,
        ImportStatus.PARTIALLY_CANCELLED,
    }:
        return current
    return None


async def _raise_if_cancelled(store, import_id: UUID) -> None:
    if await _cancellation_requested(store, import_id) is not None:
        raise _ImportCancellation


async def _resolve_import_replacements(
    worker_ctx: AppWorkerContext,
    state: ImportState,
    *,
    pod_id: UUID,
    user_id: UUID,
) -> dict[str, str]:
    if state.plan is None:
        return {}
    replacements = {
        variable.name: variable.default
        for variable in state.plan.variables
        if variable.default is not None
    }
    replacements.update(state.variables_provided or {})
    unresolved_members = [
        variable.name
        for variable in state.plan.variables
        if variable.kind == "pod_member" and not replacements.get(variable.name)
    ]
    if unresolved_members:
        member_id = await _resolve_importer_pod_member_id(
            worker_ctx, pod_id=pod_id, user_id=user_id
        )
        if member_id is not None:
            replacements.update(dict.fromkeys(unresolved_members, member_id))
    return replacements


async def _finalize_import_cancellation(store, staging, state: ImportState) -> None:
    current = await store.get_import(state.import_id) or state
    current.current_step = None
    current.status = (
        ImportStatus.PARTIALLY_CANCELLED
        if current.committed_steps
        else ImportStatus.CANCELLED
    )
    if current.status is ImportStatus.PARTIALLY_CANCELLED:
        # The status name alone was the only signal that the pod had been
        # modified. Cancelling mid-apply does not undo the steps that landed,
        # and the person who cancelled is the one who needs to know that.
        applied = len(current.committed_steps)
        total = len(current.plan.steps) if current.plan else 0
        current.error = (
            f"Cancelled after {applied} of {total} steps had been applied to "
            f"this pod. Those changes were not undone; import the bundle again "
            f"to finish, or remove what it created."
        )
    current.completed_at = _now()
    try:
        await store.save_import(current)
    except BundleStateConflictError:
        latest = await store.get_import(current.import_id)
        if latest is not None and latest.status in {
            ImportStatus.CANCELLED,
            ImportStatus.PARTIALLY_CANCELLED,
        }:
            return
        raise
    await publish_bundle_event(
        current.import_id,
        completed_payload(current.status.value, current.seq),
    )
    try:
        await staging.delete_archive("pod-imports", current.import_id)
    except Exception:  # cleanup is backstopped by the sweep job
        logger.debug(
            "pod_bundle.handlers.clean_staging_cancelled_import.diagnostic",
            import_id=str(current.import_id),
        )


async def _settle_import_state_conflict(store, staging, import_id: UUID) -> bool:
    """Resolve a CAS conflict when cancellation/finalization already won."""
    latest = await store.get_import(import_id)
    if latest is not None and latest.status is ImportStatus.CANCELLING:
        await _finalize_import_cancellation(store, staging, latest)
        return True
    return latest is not None and latest.status in {
        ImportStatus.COMPLETED,
        ImportStatus.CANCELLED,
        ImportStatus.PARTIALLY_CANCELLED,
    }


@streaq_task(name="export_pod_bundle", lane=Lane.BULK)
async def export_pod_bundle(context: dict[str, str | None]) -> None:
    worker_ctx: AppWorkerContext = streaq_worker.context
    export_id = UUID(str(context["export_id"]))
    pod_id = UUID(str(context["pod_id"]))
    user_id = UUID(str(context["user_id"]))

    store = get_pod_bundle_state_store()
    staging = BundleStagingStorage()

    state = await store.get_export(export_id)
    if state is None:
        # State was swept before the job ran (or a duplicate enqueue). Nothing to
        # do — re-running requires a fresh request.
        return
    if state.status == ExportStatus.READY:
        return

    try:
        # (a) EXPORTING
        retrying = state.status is ExportStatus.FAILED
        state.status = ExportStatus.EXPORTING
        state.error = None
        state.error_type = None
        state.error_code = None
        state.completed_at = None
        if retrying:
            state.attempt += 1
            await store.reopen_export(state)
        else:
            await store.save_export(state)
        await publish_bundle_event(
            export_id, status_payload(state.status.value, state.seq)
        )

        # (b) assemble the archive bytes inside one short UoW scope.
        async def on_progress(done: int, total: int) -> None:
            state.progress.done = done
            state.progress.total = total
            await store.save_export(state)
            await publish_bundle_event(
                export_id, progress_payload(done, total, state.seq)
            )

        async with uow_scope(worker_ctx.uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                bundle_filename, zip_bytes, warnings = await BundleExporter().export(
                    pod_id=pod_id,
                    user_id=user_id,
                    data_tables=state.data_tables,
                    file_folders=state.file_folders,
                    include=state.include,
                    ctx=ctx,
                    uow=uow,
                    on_progress=on_progress,
                )

        # (c) upload — no DB connection held.
        staging_key = await staging.put_archive("pod-exports", export_id, zip_bytes)

        # (d) READY — mint the signed download URL + retain state/archive for its TTL.
        from datetime import timedelta

        from app.modules.pod_bundle.config import pod_bundle_settings
        from app.modules.pod_bundle.infrastructure.download_url import (
            build_download_url,
        )

        ttl = state.ttl_seconds or pod_bundle_settings.pod_bundle_export_url_ttl_seconds
        state.status = ExportStatus.READY
        state.staging_key = staging_key
        state.bundle_filename = bundle_filename
        state.warnings = warnings
        state.download_url = build_download_url(
            kind="pod-exports", job_id=export_id, ttl_seconds=ttl
        )
        state.expires_at = _now() + timedelta(seconds=ttl)
        await bundle_analytics.record_bundle_exported(
            worker_ctx,
            export_id=export_id,
            pod_id=pod_id,
            user_id=user_id,
            resource_count=len(getattr(state, "manifest", None) or ()),
        )
        state.completed_at = _now()
        # Retain the READY export state (and thus its archive) for the URL's TTL,
        # longer than the default import horizon, so a shared link stays valid.
        await store.save_export(state, ttl_seconds=ttl)
        await publish_bundle_event(
            export_id,
            completed_payload(
                state.status.value,
                state.seq,
                bundle_filename=bundle_filename,
                download_url=state.download_url,
            ),
        )
    except DomainError as exc:
        # Bundle/domain errors are terminal — mark FAILED and swallow (streaq
        # retrying would fail identically).
        await _fail(store, state, str(exc))
        logger.warning(
            "pod_bundle.handlers.pod_bundle_export_s_terminal.degraded",
            export_id=export_id,
        )
    except Exception:
        # Infrastructure error (DB blip, object storage). Mark FAILED for the UI,
        # then re-raise so streaq retries with a fresh attempt.
        await _fail(store, state, "Export failed due to a transient error.")
        logger.debug(
            "pod_bundle.handlers.pod_bundle_export_s_retryable.propagated",
            export_id=export_id,
            exc_info=True,
        )
        raise


async def _fail(store, state: ExportState, message: str) -> None:
    state.status = ExportStatus.FAILED
    state.error = message
    state.completed_at = _now()
    try:
        await store.save_export(state)
        await publish_bundle_event(state.export_id, error_payload(message, state.seq))
    except Exception:  # noqa: BLE001 - failure bookkeeping is best-effort
        logger.debug(
            "pod_bundle.handlers.persist_state_export_s_s.diagnostic",
            export_id=state.export_id,
        )


@streaq_task(name="plan_pod_import", lane=Lane.BULK, origin=OriginKind.IMPORT)
async def plan_pod_import(context: dict[str, str | None]) -> None:
    """Diff a staged bundle against the pod and produce a resumable plan.

    Read-only against the DB (snapshots current resources, computes a pure diff),
    so it is safe to retry. Terminal on a malformed/missing bundle; the user
    re-uploads to try again.
    """
    worker_ctx: AppWorkerContext = streaq_worker.context
    import_id = UUID(str(context["import_id"]))

    store = get_pod_bundle_state_store()
    staging = BundleStagingStorage()

    state = await store.get_import(import_id)
    if state is None:
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        return

    try:
        await _plan_from_staging(worker_ctx, store, staging, state)
    except _ImportCancellation:
        await _finalize_import_cancellation(store, staging, state)
    except BundleStateConflictError:
        if await _settle_import_state_conflict(store, staging, import_id):
            return
        raise
    except DomainError as exc:
        await _fail_import(store, state, exc)
        logger.warning(
            "pod_bundle.handlers.pod_bundle_plan_s_terminal.degraded",
            import_id=import_id,
        )
    except Exception:
        await _fail_import(store, state, "Planning failed due to a transient error.")
        logger.debug(
            "pod_bundle.handlers.pod_bundle_plan_s_retryable.propagated",
            import_id=import_id,
            exc_info=True,
        )
        raise


async def _plan_from_staging(worker_ctx, store, staging, state: ImportState) -> None:
    """Extract the staged bundle and build a plan (shared by upload + GitHub
    imports). Sets ``PLANNING`` → ``AWAITING_CONFIRMATION``; raises a domain error
    the caller maps to a terminal FAILED state."""
    import_id = state.import_id
    await _raise_if_cancelled(store, import_id)
    state.status = ImportStatus.PLANNING
    await store.save_import(state)
    await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

    archive = await staging.get_archive("pod-imports", import_id)
    if archive is None:
        raise BundleStagingMissingError()
    await _raise_if_cancelled(store, import_id)

    with tempfile.TemporaryDirectory(prefix="lemma-pod-import-") as tmp:
        try:
            bundle_root = await extract_bundle_offloaded(archive, tmp)
        except ValueError as exc:
            raise BundleInvalidError(str(exc)) from exc
        publish_manifest.prepare_published_bundle(bundle_root)

        async with uow_scope(worker_ctx.uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=state.user_id, pod_id=state.pod_id
            )
            async with context_scope(ctx):
                from app.modules.pod_bundle.infrastructure.plan_builder import (
                    PlanBuilder,
                    ServiceExistingResources,
                )

                existing = ServiceExistingResources(
                    uow=uow, ctx=ctx, pod_id=state.pod_id, user_id=state.user_id
                )
                plan = await PlanBuilder(existing).build_plan(bundle_root=bundle_root)

    await _raise_if_cancelled(store, import_id)
    state.plan = plan
    state.progress.total = len(plan.steps)
    state.progress.done = 0
    state.status = ImportStatus.AWAITING_CONFIRMATION
    await store.save_import(state)
    await publish_bundle_event(
        import_id,
        completed_payload(state.status.value, state.seq, step_count=len(plan.steps)),
    )


@streaq_task(name="import_pod_github", lane=Lane.BULK, origin=OriginKind.IMPORT)
async def import_pod_github(context: dict[str, str | None]) -> None:
    """Fetch a GitHub zipball, using the selected connector account when set."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    import_id = UUID(str(context["import_id"]))

    store = get_pod_bundle_state_store()
    staging = BundleStagingStorage()

    state = await store.get_import(import_id)
    if state is None:
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        return

    try:
        if state.retryable or state.error is not None:
            state.attempt += 1
        state.error = None
        state.error_type = None
        state.error_code = None
        state.retryable = False
        await _raise_if_cancelled(store, import_id)
        state.status = ImportStatus.FETCHING
        await store.save_import(state)
        await publish_bundle_event(
            import_id, status_payload(state.status.value, state.seq)
        )

        owner, repo = github_fetcher.parse_repo_ref(
            repo_url=state.source.repo_url,
            owner=(context.get("owner")),
            repo=(context.get("repo")),
        )
        zip_bytes = await _github_import_fetcher(worker_ctx, state).fetch_zipball(
            owner=owner, repo=repo, ref=state.source.ref
        )
        await _raise_if_cancelled(store, import_id)
        state.staging_key = await staging.put_archive(
            "pod-imports", import_id, zip_bytes
        )
        await store.save_import(state)

        await _plan_from_staging(worker_ctx, store, staging, state)
    except _ImportCancellation:
        await _finalize_import_cancellation(store, staging, state)
    except BundleStateConflictError:
        if await _settle_import_state_conflict(store, staging, import_id):
            return
        raise
    except DomainError as exc:
        if _is_retryable_import_error(exc) and state.attempt < 3:
            await _record_import_retry(store, state, exc)
            raise StreaqRetry(delay=min(state.attempt**2, 9)) from exc
        await _fail_import(store, state, exc)
        logger.warning(
            "pod_bundle.handlers.github_import_s_terminal_s.degraded",
            import_id=import_id,
        )
    except Exception as exc:
        if state.attempt < 3:
            await _record_import_retry(store, state, exc)
            raise StreaqRetry(delay=min(state.attempt**2, 9)) from exc
        await _fail_import(
            store,
            state,
            exc,
            public_message="GitHub import failed after three transient attempts.",
        )
        logger.debug(
            "pod_bundle.handlers.github_import_s_retryable_s.propagated",
            import_id=import_id,
            exc_info=True,
        )


@streaq_task(name="import_pod_url", lane=Lane.BULK, origin=OriginKind.IMPORT)
async def import_pod_url(context: dict[str, str | None]) -> None:
    """Copy a lemma-origin source object (an export or an uploaded bundle) into
    this import's own staging, then plan — one job per ``import_id``. The source
    is read straight from object storage (verified at start), so there is no
    server-side HTTP fetch and no SSRF surface. Making the import self-contained
    lets the source expire independently."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    import_id = UUID(str(context["import_id"]))
    source_kind = str(context["source_kind"])
    source_id = UUID(str(context["source_id"]))

    store = get_pod_bundle_state_store()
    staging = BundleStagingStorage()

    state = await store.get_import(import_id)
    if state is None:
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        return

    try:
        await _raise_if_cancelled(store, import_id)
        state.status = ImportStatus.FETCHING
        await store.save_import(state)
        await publish_bundle_event(
            import_id, status_payload(state.status.value, state.seq)
        )

        data = await staging.get_archive(source_kind, source_id)  # type: ignore[arg-type]
        if data is None:
            raise BundleStagingMissingError(
                "The source bundle is no longer available; export or upload it again."
            )
        await _raise_if_cancelled(store, import_id)
        state.staging_key = await staging.put_archive("pod-imports", import_id, data)
        await store.save_import(state)

        await _plan_from_staging(worker_ctx, store, staging, state)
    except _ImportCancellation:
        await _finalize_import_cancellation(store, staging, state)
    except BundleStateConflictError:
        if await _settle_import_state_conflict(store, staging, import_id):
            return
        raise
    except DomainError as exc:
        await _fail_import(store, state, exc)
        logger.warning(
            "pod_bundle.handlers.url_import_s_terminal_s.degraded", import_id=import_id
        )
    except Exception:
        await _fail_import(store, state, "URL import failed due to a transient error.")
        logger.debug(
            "pod_bundle.handlers.url_import_s_retryable_s.propagated",
            import_id=import_id,
            exc_info=True,
        )
        raise


@streaq_task(name="apply_pod_import", lane=Lane.BULK, origin=OriginKind.IMPORT)
async def apply_pod_import(context: dict[str, str | None]) -> None:
    """Apply an approved plan step by step: each step runs in its own short UoW
    (commit) then a Redis checkpoint, so a crash resumes from the first pending
    step and the idempotent upserts converge. Records a recipe on the pod when
    every step lands."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    import_id = UUID(str(context["import_id"]))
    pod_id = UUID(str(context["pod_id"]))
    user_id = UUID(str(context["user_id"]))

    store = get_pod_bundle_state_store()
    staging = BundleStagingStorage()

    state = await store.get_import(import_id)
    if state is None or state.plan is None:
        return
    if state.status == ImportStatus.COMPLETED:
        return
    if state.status == ImportStatus.CANCELLING:
        await _finalize_import_cancellation(store, staging, state)
        return

    from app.modules.pod_bundle.infrastructure.app_builder import AppStepRunner
    from app.modules.pod_bundle.infrastructure.function_builder import (
        FunctionStepRunner,
    )
    from app.modules.pod_bundle.infrastructure.applier import (
        BundleApplier,
        StepNotApplicableError,
    )
    from app.modules.pod_bundle.domain.state import StepKind, StepStatus

    try:
        await _raise_if_cancelled(store, import_id)
        state.status = ImportStatus.APPLYING
        await store.save_import(state)
        await publish_bundle_event(
            import_id, status_payload(state.status.value, state.seq)
        )

        archive = await staging.get_archive("pod-imports", import_id)
        if archive is None:
            raise BundleStagingMissingError()

        replacements = await _resolve_import_replacements(
            worker_ctx, state, pod_id=pod_id, user_id=user_id
        )

        # APP steps build in a sandbox and must not hold a pooled DB connection,
        # so they run through a self-scoped runner instead of the per-step uow_scope.
        app_runner = AppStepRunner(uow_factory=worker_ctx.uow_factory)
        function_runner = None

        with tempfile.TemporaryDirectory(prefix="lemma-pod-apply-") as tmp:
            try:
                bundle_root = await extract_bundle_offloaded(archive, tmp)
            except ValueError as exc:
                raise BundleInvalidError(str(exc)) from exc
            publish_manifest.prepare_published_bundle(bundle_root)

            while (step := state.plan.next_pending_step()) is not None:
                await _raise_if_cancelled(store, import_id)
                step.status = StepStatus.RUNNING
                state.current_step = step.index
                await store.save_import(state)
                try:
                    if step.kind in {StepKind.APP, StepKind.FUNCTION}:
                        # Self-scoped: creates the app, builds it in a sandbox
                        # (no connection held), then deploys — managing its own short
                        # UoWs. Idempotent-by-name + dist sha256 dedup, so a replay
                        # after a crash converges.
                        if step.kind is StepKind.APP:
                            runner = app_runner
                        else:
                            if function_runner is None:
                                function_runner = FunctionStepRunner(
                                    uow_factory=worker_ctx.uow_factory
                                )
                            runner = function_runner
                        await runner.run(
                            step,
                            pod_id=pod_id,
                            user_id=user_id,
                            bundle_root=bundle_root,
                            replacements=replacements,
                        )
                        cancelled = await _cancellation_requested(store, import_id)
                        if cancelled is not None:
                            if step.index not in cancelled.committed_steps:
                                cancelled.committed_steps.append(step.index)
                            if cancelled.plan is not None:
                                cancelled_step = next(
                                    (
                                        item
                                        for item in cancelled.plan.steps
                                        if item.index == step.index
                                    ),
                                    None,
                                )
                                if cancelled_step is not None:
                                    cancelled_step.status = StepStatus.DONE
                            await _finalize_import_cancellation(
                                store, staging, cancelled
                            )
                            return
                    else:
                        async with uow_scope(worker_ctx.uow_factory) as uow:
                            ctx = await AuthorizationDataService(
                                uow.session
                            ).build_user_context(user_id=user_id, pod_id=pod_id)
                            async with context_scope(ctx):
                                applier = BundleApplier(
                                    uow=uow,
                                    ctx=ctx,
                                    pod_id=pod_id,
                                    user_id=user_id,
                                    bundle_root=bundle_root,
                                    replacements=replacements,
                                    # The live list: a best-effort fallback the
                                    # applier takes is checkpointed with the step
                                    # and read back by the status endpoint.
                                    warnings=state.warnings,
                                )
                                await applier.apply_step(step)
                                await _raise_if_cancelled(store, import_id)
                                # Commit the step before checkpointing it DONE: the
                                # bare UoW rolls back on error but does NOT auto-commit
                                # on success, so uncommitted writes (e.g. a workflow a
                                # later schedule step must resolve) would otherwise be
                                # lost — and a DONE checkpoint on lost data would break
                                # crash-resume. Idempotent when the service already
                                # committed internally.
                                await uow.commit()
                    step.status = StepStatus.DONE
                    if step.index not in state.committed_steps:
                        state.committed_steps.append(step.index)
                except StepNotApplicableError as exc:
                    # Deferred kind (app/surface/grants) — skip, don't fail.
                    step.status = StepStatus.SKIPPED
                    step.error = str(exc)
                except DomainError as exc:
                    step.status = StepStatus.FAILED
                    step.error = str(exc)
                    await _checkpoint(store, state, step)
                    await _fail_import(
                        store,
                        state,
                        exc,
                        public_message=_apply_failure_message(state, step, exc),
                    )
                    logger.debug(
                        "pod_bundle.handlers.import_s_step_s_s.diagnostic",
                        import_id=import_id,
                    )
                    return
                await _checkpoint(store, state, step)

        await _raise_if_cancelled(store, import_id)
        await _record_recipe(worker_ctx, state)
        await _raise_if_cancelled(store, import_id)
        state.status = ImportStatus.COMPLETED
        state.completed_at = _now()
        await store.save_import(state)
        await bundle_analytics.record_import_completed(
            worker_ctx,
            import_id=import_id,
            pod_id=pod_id,
            user_id=user_id,
            resource_count=len(getattr(state.plan, "steps", None) or ()),
            is_remix=bool(getattr(state, "is_remix", False)),
        )
        await publish_bundle_event(
            import_id, completed_payload(state.status.value, state.seq)
        )
        # Best-effort cleanup; the sweep cron backstops.
        try:
            await staging.delete_archive("pod-imports", import_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "pod_bundle.handlers.delete_staged_import_s_s.diagnostic",
                import_id=import_id,
            )
    except _ImportCancellation:
        await _finalize_import_cancellation(store, staging, state)
    except BundleStateConflictError:
        if await _settle_import_state_conflict(store, staging, import_id):
            return
        raise
    except DomainError as exc:
        await _fail_import(store, state, exc)
        logger.warning(
            "pod_bundle.handlers.pod_bundle_apply_s_terminal.degraded",
            import_id=import_id,
        )
    except Exception:
        await _fail_import(store, state, "Apply failed due to a transient error.")
        logger.debug(
            "pod_bundle.handlers.pod_bundle_apply_s_retryable.propagated",
            import_id=import_id,
            exc_info=True,
        )
        raise


def _apply_failure_message(state: ImportState, step, exc: Exception) -> str:
    """Say what the pod now holds, not just which step broke.

    Apply commits each step in its own unit of work and there is no rollback, so
    a failure part-way through an approved plan leaves the pod changed. Telling
    the person only "Step X failed" leaves them with half a pod, no idea that it
    is half a pod, and no stated way forward -- while re-applying does in fact
    resume from the failed step rather than duplicating the work already done.
    """
    applied = len(state.committed_steps)
    total = len(state.plan.steps) if state.plan else 0
    head = f"Step '{step.name}' failed: {exc}"
    if not applied:
        return f"{head} Nothing was applied to the pod."
    return (
        f"{head} {applied} of {total} steps were already applied to this pod and "
        f"were not undone; apply this import again to continue from step "
        f"'{step.name}'."
    )


async def _checkpoint(store, state: ImportState, step) -> None:
    assert state.plan is not None
    done = sum(1 for s in state.plan.steps if s.status.value in ("DONE", "SKIPPED"))
    state.progress.done = done
    state.progress.total = len(state.plan.steps)
    state.current_step = None
    await store.save_import(state)
    await publish_bundle_event(
        state.import_id,
        step_payload(
            {
                "index": step.index,
                "kind": step.kind.value,
                "name": step.name,
                "action": step.action.value,
                "status": step.status.value,
                "error": step.error,
            },
            state.seq,
        ),
    )


async def _resolve_importer_pod_member_id(
    worker_ctx: AppWorkerContext, *, pod_id: UUID, user_id: UUID
) -> str | None:
    """The importing user's own pod-member id in the target pod — what a
    ``pod_member`` (``${..._assignee}``) variable resolves to when the importer
    doesn't supply one explicitly, matching the CLI's assignee resolution.

    Best-effort: returns ``None`` (leaving the placeholder unresolved, so the
    service drops the assignee) if the user has no membership or the lookup
    fails, rather than failing the whole apply over one workflow assignee."""
    from app.composition.pod_bundle_pod import get_pod_member_service

    try:
        async with uow_scope(worker_ctx.uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                service = get_pod_member_service(uow)
                member = await service.get_pod_member_by_user_id(
                    pod_id, user_id, requester_user_id=user_id
                )
                return str(member.id)
    except Exception:  # noqa: BLE001 — assignee auto-resolution is best-effort
        logger.debug(
            "pod_bundle.handlers.could_not_resolve_importer_pod.diagnostic",
            pod_id=pod_id,
            user_id=user_id,
        )
        return None


async def _record_recipe(worker_ctx: AppWorkerContext, state: ImportState) -> None:
    """Append a durable :class:`PodRecipe` to the pod's config in a short UoW.

    Copies the existing typed config and overrides only ``recipes`` so the
    shallow config merge in ``PodService.update_pod`` cannot reset unrelated
    fields (join_policy, default_runtime) to their defaults."""
    from datetime import datetime, timezone

    from app.composition.pod_bundle_pod import get_pod_service
    from app.modules.pod.contracts import (
        PodRecipe,
        PodUpdateEntity,
    )

    recipe = PodRecipe(
        kind=state.source.kind.value,
        name=(state.plan.bundle_name if state.plan else None),
        repo_url=state.source.repo_url or state.source.url,
        format_version=(state.plan.format_version if state.plan else None),
        imported_at=datetime.now(timezone.utc),
        imported_by=state.user_id,
    )
    async with uow_scope(worker_ctx.uow_factory) as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=state.user_id, pod_id=state.pod_id
        )
        async with context_scope(ctx):
            pod_service = get_pod_service(uow)
            pod = await pod_service.get_pod(state.pod_id, state.user_id)
            assert pod is not None
            new_config = pod.config.model_copy(
                update={"recipes": [*pod.config.recipes, recipe]}
            )
            await pod_service.update_pod(
                state.pod_id,
                PodUpdateEntity(config=new_config),
                requester_user_id=state.user_id,
                ctx=ctx,
            )


def _is_retryable_import_error(exc: DomainError) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code in {408, 429} or (
        isinstance(status_code, int) and status_code >= 500
    )


async def _record_import_retry(
    store,
    state: ImportState,
    exc: Exception,
) -> None:
    state.error = "GitHub is temporarily unavailable; retrying import."
    state.error_type = type(exc).__name__
    state.error_code = str(getattr(exc, "code", None) or "GITHUB_IMPORT_TRANSIENT")
    state.retryable = True
    await store.save_import(state)


def _github_import_operation_runner(
    *,
    worker_ctx: AppWorkerContext,
    state: ImportState,
):
    async def run(operation_name: str, payload: dict) -> dict:
        async with uow_scope(worker_ctx.uow_factory) as uow:
            actor = await AuthorizationDataService(uow.session).build_user_context(
                user_id=state.user_id,
                pod_id=state.pod_id,
            )
            from app.composition.pod_bundle_resources import (
                build_connector_operation_service,
            )

            service = build_connector_operation_service(uow)
            response = await service.execute_operation(
                connector_id="github",
                operation_name=operation_name,
                payload=payload,
                user_id=state.user_id,
                actor=actor,
                account_id=state.account_id,
            )
        if hasattr(response, "model_dump"):
            return response.model_dump(mode="json")
        return response if isinstance(response, dict) else {}

    return run


async def _fail_import(
    store,
    state: ImportState,
    error: str | Exception,
    *,
    public_message: str | None = None,
) -> None:
    state.status = ImportStatus.FAILED
    state.error = public_message or str(error)
    state.error_type = type(error).__name__ if isinstance(error, Exception) else None
    state.error_code = str(getattr(error, "code", None) or "POD_BUNDLE_IMPORT_FAILED")
    state.retryable = False
    state.completed_at = _now()
    try:
        await store.save_import(state)
        await publish_bundle_event(
            state.import_id,
            error_payload(state.error, state.seq),
        )
    except Exception:  # noqa: BLE001 - failure bookkeeping is best-effort
        logger.debug(
            "pod_bundle.handlers.persist_state_import_s_s.diagnostic",
            import_id=state.import_id,
        )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
