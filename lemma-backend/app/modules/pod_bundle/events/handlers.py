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
    streaq_task,
    streaq_worker,
)
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import BundleInvalidError, BundleStagingMissingError
from app.modules.pod_bundle.domain.state import (
    ExportState,
    ExportStatus,
    ImportState,
    ImportStatus,
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
        logger.info("Export state missing; skipping export job %s", export_id)
        return
    if state.is_terminal:
        logger.info("Export %s already terminal (%s); skipping", export_id, state.status)
        return

    try:
        # (a) EXPORTING
        state.status = ExportStatus.EXPORTING
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
                bundle_filename, zip_bytes = await BundleExporter().export(
                    pod_id=pod_id,
                    user_id=user_id,
                    with_data=state.with_data,
                    include=state.include,
                    ctx=ctx,
                    uow=uow,
                    on_progress=on_progress,
                )

        # (c) upload — no DB connection held.
        staging_key = await staging.put_archive("pod-exports", export_id, zip_bytes)

        # (d) READY
        state.status = ExportStatus.READY
        state.staging_key = staging_key
        state.bundle_filename = bundle_filename
        state.completed_at = _now()
        await store.save_export(state)
        await publish_bundle_event(
            export_id,
            completed_payload(
                state.status.value,
                state.seq,
                bundle_filename=bundle_filename,
            ),
        )
    except DomainError as exc:
        # Bundle/domain errors are terminal — mark FAILED and swallow (streaq
        # retrying would fail identically).
        await _fail(store, state, str(exc))
        logger.warning("Pod bundle export %s failed (terminal): %s", export_id, exc)
    except Exception as exc:
        # Infrastructure error (DB blip, object storage). Mark FAILED for the UI,
        # then re-raise so streaq retries with a fresh attempt.
        await _fail(store, state, "Export failed due to a transient error.")
        logger.error("Pod bundle export %s failed (retryable): %s", export_id, exc)
        raise


async def _fail(store, state: ExportState, message: str) -> None:
    state.status = ExportStatus.FAILED
    state.error = message
    state.completed_at = _now()
    try:
        await store.save_export(state)
        await publish_bundle_event(
            state.export_id, error_payload(message, state.seq)
        )
    except Exception as exc:  # noqa: BLE001 - failure bookkeeping is best-effort
        logger.warning(
            "Failed to persist FAILED state for export %s: %s", state.export_id, exc
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
        logger.info("Import state missing; skipping plan job %s", import_id)
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        logger.info("Import %s already at %s; skipping plan", import_id, state.status)
        return

    try:
        await _plan_from_staging(worker_ctx, store, staging, state)
    except DomainError as exc:
        await _fail_import(store, state, str(exc))
        logger.warning("Pod bundle plan %s failed (terminal): %s", import_id, exc)
    except Exception as exc:
        await _fail_import(store, state, "Planning failed due to a transient error.")
        logger.error("Pod bundle plan %s failed (retryable): %s", import_id, exc)
        raise


async def _plan_from_staging(worker_ctx, store, staging, state: ImportState) -> None:
    """Extract the staged bundle and build a plan (shared by upload + GitHub
    imports). Sets ``PLANNING`` → ``AWAITING_CONFIRMATION``; raises a domain error
    the caller maps to a terminal FAILED state."""
    import_id = state.import_id
    state.status = ImportStatus.PLANNING
    await store.save_import(state)
    await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

    archive = await staging.get_archive("pod-imports", import_id)
    if archive is None:
        raise BundleStagingMissingError()

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
        logger.info("Import state missing; skipping github job %s", import_id)
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        return

    try:
        state.status = ImportStatus.FETCHING
        await store.save_import(state)
        await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

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
        state.staging_key = await staging.put_archive("pod-imports", import_id, zip_bytes)
        await store.save_import(state)

        await _plan_from_staging(worker_ctx, store, staging, state)
    except DomainError as exc:
        await _fail_import(store, state, str(exc))
        logger.warning("GitHub import %s failed (terminal): %s", import_id, exc)
    except Exception as exc:
        await _fail_import(store, state, "GitHub import failed due to a transient error.")
        logger.error("GitHub import %s failed (retryable): %s", import_id, exc)
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
        logger.info("Import %s has no plan; skipping apply", import_id)
        return
    if state.status == ImportStatus.COMPLETED:
        return

    from app.modules.pod_bundle.infrastructure.applier import (
        BundleApplier,
        StepNotApplicableError,
    )
    from app.modules.pod_bundle.domain.state import StepStatus

    try:
        state.status = ImportStatus.APPLYING
        await store.save_import(state)
        await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

        archive = await staging.get_archive("pod-imports", import_id)
        if archive is None:
            raise BundleStagingMissingError()

        replacements = dict(state.variables_provided or {})

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
                step.status = StepStatus.RUNNING
                try:
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
                    step.status = StepStatus.DONE
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
                    logger.warning("Import %s step %s failed: %s", import_id, step.name, exc)
                    return
                await _checkpoint(store, state, step)

        await _record_recipe(worker_ctx, state)
        state.status = ImportStatus.COMPLETED
        state.completed_at = _now()
        await store.save_import(state)
        await publish_bundle_event(
            import_id, completed_payload(state.status.value, state.seq)
        )
        # Best-effort cleanup; the sweep cron backstops.
        try:
            await staging.delete_archive("pod-imports", import_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete staged import %s: %s", import_id, exc)
    except DomainError as exc:
        await _fail_import(store, state, str(exc))
        logger.warning("Pod bundle apply %s failed (terminal): %s", import_id, exc)
    except Exception as exc:
        await _fail_import(store, state, "Apply failed due to a transient error.")
        logger.error("Pod bundle apply %s failed (retryable): %s", import_id, exc)
        raise


async def _checkpoint(store, state: ImportState, step) -> None:
    done = sum(1 for s in state.plan.steps if s.status.value in ("DONE", "SKIPPED"))
    state.progress.done = done
    state.progress.total = len(state.plan.steps)
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


async def _record_recipe(worker_ctx: AppWorkerContext, state: ImportState) -> None:
    """Append a durable :class:`PodRecipe` to the pod's config in a short UoW.

    Copies the existing typed config and overrides only ``recipes`` so the
    shallow config merge in ``PodService.update_pod`` cannot reset unrelated
    fields (join_policy, default_runtime) to their defaults."""
    from datetime import datetime, timezone

    from app.modules.pod.api.dependencies import get_pod_service
    from app.modules.pod.domain.pod_entities import (
        PodRecipe,
        PodUpdateEntity,
    )

    recipe = PodRecipe(
        kind=state.source.kind,
        name=(state.plan.bundle_name if state.plan else None),
        repo_url=state.source.repo_url,
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
        await publish_bundle_event(
            state.import_id, error_payload(message, state.seq)
        )
    except Exception as exc:  # noqa: BLE001 - failure bookkeeping is best-effort
        logger.warning(
            "Failed to persist FAILED state for import %s: %s", state.import_id, exc
        )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
