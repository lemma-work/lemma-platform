"""Streaq tasks for pod bundle jobs.

Imported for side effects by ``module.register_streaq`` at worker startup.
Tasks land slice by slice: export, plan, apply, GitHub import, publish, sweep.

Export job phases (see ``docs/design/pod-bundle-share-import.md``):
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
from pathlib import Path
from uuid import UUID

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_cron,
    streaq_task,
    streaq_worker,
)
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
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
    PublishStatus,
)
from app.modules.pod_bundle.infrastructure.exporter import BundleExporter
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
            'pod_bundle.handlers.clean_staging_cancelled_import.diagnostic',
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


@streaq_task(name="export_pod_bundle")
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
                    with_data=state.with_data,
                    data_tables=state.data_tables,
                    with_files=state.with_files,
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
            'pod_bundle.handlers.pod_bundle_export_s_retryable.propagated',
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
            'pod_bundle.handlers.persist_state_export_s_s.diagnostic',
            export_id=state.export_id,
        )


@streaq_task(name="plan_pod_import")
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
        await _fail_import(store, state, str(exc))
        logger.warning(
            "pod_bundle.handlers.pod_bundle_plan_s_terminal.degraded",
            import_id=import_id,
        )
    except Exception:
        await _fail_import(store, state, "Planning failed due to a transient error.")
        logger.debug(
            'pod_bundle.handlers.pod_bundle_plan_s_retryable.propagated',
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
        from lemma_pod_bundle import extract_bundle

        try:
            bundle_root = extract_bundle(
                archive,
                Path(tmp),
                max_uncompressed_bytes=pod_bundle_settings.pod_bundle_max_uncompressed_bytes,
            )
        except ValueError as exc:
            raise BundleInvalidError(str(exc)) from exc

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


@streaq_task(name="import_pod_github")
async def import_pod_github(context: dict[str, str | None]) -> None:
    """Fetch a public repo's zipball, stage it, then plan — one job, so a single
    ``import_id`` covers fetch + plan. Falls through to the same planning routine
    as an uploaded bundle."""
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
        await _raise_if_cancelled(store, import_id)
        state.status = ImportStatus.FETCHING
        await store.save_import(state)
        await publish_bundle_event(
            import_id, status_payload(state.status.value, state.seq)
        )

        from app.modules.pod_bundle.infrastructure.github_fetcher import (
            GithubBundleFetcher,
            parse_repo_ref,
        )

        owner, repo = parse_repo_ref(
            repo_url=state.source.repo_url,
            owner=(context.get("owner")),
            repo=(context.get("repo")),
        )
        zip_bytes = await GithubBundleFetcher().fetch_zipball(
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
        await _fail_import(store, state, str(exc))
        logger.warning(
            "pod_bundle.handlers.github_import_s_terminal_s.degraded",
            import_id=import_id,
        )
    except Exception:
        await _fail_import(
            store, state, "GitHub import failed due to a transient error."
        )
        logger.debug(
            'pod_bundle.handlers.github_import_s_retryable_s.propagated',
            import_id=import_id,
        exc_info=True,
    )
        raise


@streaq_task(name="import_pod_url")
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
        await _fail_import(store, state, str(exc))
        logger.warning(
            "pod_bundle.handlers.url_import_s_terminal_s.degraded", import_id=import_id
        )
    except Exception:
        await _fail_import(store, state, "URL import failed due to a transient error.")
        logger.debug(
            'pod_bundle.handlers.url_import_s_retryable_s.propagated', import_id=import_id,
        exc_info=True,
    )
        raise


@streaq_task(name="apply_pod_import")
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

        # APP steps build in the agentbox and must not hold a pooled DB connection,
        # so they run through a self-scoped runner instead of the per-step uow_scope.
        app_runner = AppStepRunner(uow_factory=worker_ctx.uow_factory)

        with tempfile.TemporaryDirectory(prefix="lemma-pod-apply-") as tmp:
            from lemma_pod_bundle import extract_bundle

            try:
                bundle_root = extract_bundle(
                    archive,
                    Path(tmp),
                    max_uncompressed_bytes=pod_bundle_settings.pod_bundle_max_uncompressed_bytes,
                )
            except ValueError as exc:
                raise BundleInvalidError(str(exc)) from exc

            while (step := state.plan.next_pending_step()) is not None:
                await _raise_if_cancelled(store, import_id)
                step.status = StepStatus.RUNNING
                state.current_step = step.index
                await store.save_import(state)
                try:
                    if step.kind is StepKind.APP:
                        # Self-scoped: creates the app, builds it in the agentbox
                        # (no connection held), then deploys — managing its own short
                        # UoWs. Idempotent-by-name + dist sha256 dedup, so a replay
                        # after a crash converges.
                        await app_runner.run(
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
                        store, state, f"Step '{step.name}' failed: {exc}"
                    )
                    logger.debug(
                        'pod_bundle.handlers.import_s_step_s_s.diagnostic',
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
        await publish_bundle_event(
            import_id, completed_payload(state.status.value, state.seq)
        )
        # Best-effort cleanup; the sweep cron backstops.
        try:
            await staging.delete_archive("pod-imports", import_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                'pod_bundle.handlers.delete_staged_import_s_s.diagnostic',
                import_id=import_id,
            )
    except _ImportCancellation:
        await _finalize_import_cancellation(store, staging, state)
    except BundleStateConflictError:
        if await _settle_import_state_conflict(store, staging, import_id):
            return
        raise
    except DomainError as exc:
        await _fail_import(store, state, str(exc))
        logger.warning(
            "pod_bundle.handlers.pod_bundle_apply_s_terminal.degraded",
            import_id=import_id,
        )
    except Exception:
        await _fail_import(store, state, "Apply failed due to a transient error.")
        logger.debug(
            'pod_bundle.handlers.pod_bundle_apply_s_retryable.propagated',
            import_id=import_id,
        exc_info=True,
    )
        raise


async def _checkpoint(store, state: ImportState, step) -> None:
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
            'pod_bundle.handlers.could_not_resolve_importer_pod.diagnostic',
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
            new_config = pod.config.model_copy(
                update={"recipes": [*pod.config.recipes, recipe]}
            )
            await pod_service.update_pod(
                state.pod_id,
                PodUpdateEntity(config=new_config),
                requester_user_id=state.user_id,
                ctx=ctx,
            )


async def _fail_import(store, state: ImportState, message: str) -> None:
    state.status = ImportStatus.FAILED
    state.error = message
    state.completed_at = _now()
    try:
        await store.save_import(state)
        await publish_bundle_event(state.import_id, error_payload(message, state.seq))
    except Exception:  # noqa: BLE001 - failure bookkeeping is best-effort
        logger.debug(
            'pod_bundle.handlers.persist_state_import_s_s.diagnostic',
            import_id=state.import_id,
        )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# A non-terminal job untouched for longer than this is presumed dead (worker
# crash/restart): the apply job's own timeout is 1800s, so ~40min leaves a wide
# margin before the sweep intervenes.
_STUCK_AFTER_SECONDS = 40 * 60


@streaq_cron("*/30 * * * *", name="sweep_pod_bundle_staging")
async def sweep_pod_bundle_staging() -> None:
    """Reclaim staged archives whose ephemeral state has expired, and mark
    crashed jobs FAILED so the UI stops showing them as in-progress. Durable
    recovery scans PostgreSQL first, independently of object-store inventory."""
    reclaimed, recovered = await _sweep(
        get_pod_bundle_state_store(), BundleStagingStorage()
    )
    if reclaimed or recovered:
        logger.debug("pod_bundle.handlers.pod_bundle_sweep_reclaimed_d.observed")


async def _sweep(store, staging) -> tuple[int, int]:
    # Per-kind retention is driven by the state TTL, not this cron: a READY
    # export is written with the export TTL (default 24h) while imports use the
    # default ~6h, so an export's state (and thus its archive, reclaimed only
    # once the state is gone) naturally outlives an import's.
    from datetime import timedelta

    cutoff = _now() - timedelta(seconds=_STUCK_AFTER_SECONDS)

    reclaimed = 0
    recovered_states = await store.recover_stale_jobs(cutoff=cutoff)
    recovered = len(recovered_states)
    for state in recovered_states:
        job_id = getattr(
            state,
            "import_id",
            getattr(state, "export_id", getattr(state, "publish_id", None)),
        )
        if job_id is not None:
            await publish_bundle_event(
                job_id,
                error_payload(state.error or "Job interrupted.", state.seq),
            )
    for kind, get_state, save_state, failed_status in (
        ("pod-imports", store.get_import, store.save_import, ImportStatus.FAILED),
        ("pod-exports", store.get_export, store.save_export, ExportStatus.FAILED),
        (
            "pod-publishes",
            store.get_publish,
            store.save_publish,
            PublishStatus.FAILED,
        ),
    ):
        try:
            archives = await staging.list_archives(kind)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            logger.debug('pod_bundle.handlers.sweep_could_not_list_s.diagnostic')
            continue
        for job_id, _ in archives:
            state = await get_state(job_id)
            if state is None:
                # State TTL expired → the job is unreferenceable; reclaim bytes.
                try:
                    await staging.delete_archive(kind, job_id)  # type: ignore[arg-type]
                    reclaimed += 1
                except Exception:  # noqa: BLE001
                    logger.debug(
                        'pod_bundle.handlers.sweep_delete_s_s_s.diagnostic', job_id=job_id
                    )
                continue
            if not state.is_terminal and state.updated_at < cutoff:
                state.status = failed_status
                state.error = "Interrupted (worker restart or crash); start over."
                state.completed_at = _now()
                await save_state(state)  # type: ignore[arg-type]
                await publish_bundle_event(
                    job_id, error_payload(state.error, state.seq)
                )
                recovered += 1

    return reclaimed, recovered
