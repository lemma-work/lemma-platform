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
from app.core.concurrency.offload import run_blocking
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
from app.modules.pod_bundle.domain.errors import BundleInvalidError, BundleStagingMissingError
from app.modules.pod_bundle.domain.state import (
    ExportState,
    ExportStatus,
    ImportState,
    ImportStatus,
    PublishState,
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
        from app.modules.pod_bundle.infrastructure.download_url import build_download_url

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
        logger.info("Import state missing; skipping url job %s", import_id)
        return
    if state.is_terminal or state.status == ImportStatus.AWAITING_CONFIRMATION:
        return

    try:
        state.status = ImportStatus.FETCHING
        await store.save_import(state)
        await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

        data = await staging.get_archive(source_kind, source_id)  # type: ignore[arg-type]
        if data is None:
            raise BundleStagingMissingError(
                "The source bundle is no longer available; export or upload it again."
            )
        state.staging_key = await staging.put_archive("pod-imports", import_id, data)
        await store.save_import(state)

        await _plan_from_staging(worker_ctx, store, staging, state)
    except DomainError as exc:
        await _fail_import(store, state, str(exc))
        logger.warning("URL import %s failed (terminal): %s", import_id, exc)
    except Exception as exc:
        await _fail_import(store, state, "URL import failed due to a transient error.")
        logger.error("URL import %s failed (retryable): %s", import_id, exc)
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

    from app.modules.pod_bundle.infrastructure.app_builder import AppStepRunner
    from app.modules.pod_bundle.infrastructure.applier import (
        BundleApplier,
        StepNotApplicableError,
    )
    from app.modules.pod_bundle.domain.state import StepKind, StepStatus

    try:
        state.status = ImportStatus.APPLYING
        await store.save_import(state)
        await publish_bundle_event(import_id, status_payload(state.status.value, state.seq))

        archive = await staging.get_archive("pod-imports", import_id)
        if archive is None:
            raise BundleStagingMissingError()

        # Resolve ${var} placeholders from the plan's defaults first, then the
        # importer-provided values (which win). Required variables are validated at
        # apply-request time, so anything still unresolved here is an optional var
        # with no default and is dropped by the service layer.
        replacements = {
            v.name: v.default for v in state.plan.variables if v.default is not None
        }
        replacements.update(state.variables_provided or {})

        # A pod_member (``${..._assignee}``) variable auto-resolves to the
        # importing user's own membership unless they supplied one — otherwise the
        # placeholder is left unresolved and the workflow silently loses its
        # assignee. Resolve once and reuse for every such variable.
        unresolved_member_vars = [
            v.name
            for v in state.plan.variables
            if v.kind == "pod_member" and not replacements.get(v.name)
        ]
        if unresolved_member_vars:
            member_id = await _resolve_importer_pod_member_id(
                worker_ctx, pod_id=pod_id, user_id=user_id
            )
            if member_id is not None:
                for name in unresolved_member_vars:
                    replacements[name] = member_id

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
                step.status = StepStatus.RUNNING
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
                                # Commit the step before checkpointing it DONE: the
                                # bare UoW rolls back on error but does NOT auto-commit
                                # on success, so uncommitted writes (e.g. a workflow a
                                # later schedule step must resolve) would otherwise be
                                # lost — and a DONE checkpoint on lost data would break
                                # crash-resume. Idempotent when the service already
                                # committed internally.
                                await uow.commit()
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


@streaq_task(name="publish_pod_github")
async def publish_pod_github(context: dict[str, str | None]) -> None:
    """Export the pod, render a README, and push everything to a new GitHub repo
    via the Composio connector — per-file checkpoints make it resumable and a
    missing GitHub connection ends terminally with a clear message."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    publish_id = UUID(str(context["publish_id"]))
    pod_id = UUID(str(context["pod_id"]))
    user_id = UUID(str(context["user_id"]))

    store = get_pod_bundle_state_store()
    state = await store.get_publish(publish_id)
    if state is None or state.status == PublishStatus.COMPLETED:
        return

    from app.modules.pod_bundle.infrastructure.ai_readme import (
        build_system_polish_fn,
        polish_readme,
    )
    from app.modules.pod_bundle.infrastructure.exporter import BundleExporter
    from app.modules.pod_bundle.infrastructure.github_publisher import (
        ComposioGithubOps,
        GithubPublisher,
    )
    from app.modules.pod_bundle.infrastructure.readme import render_readme

    try:
        # (1) EXPORTING — assemble the bundle bytes in one short UoW scope.
        state.status = PublishStatus.EXPORTING
        await store.save_publish(state)
        await publish_bundle_event(publish_id, status_payload(state.status.value, state.seq))

        async def _noop_progress(done: int, total: int) -> None:
            return None

        organization_id = None
        async with uow_scope(worker_ctx.uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            organization_id = ctx.organization_id
            async with context_scope(ctx):
                # Resources only: a pod published to (possibly public) GitHub ships
                # a template that recreates the pod in an empty-table state, never a
                # dump of its row data.
                pod_name, zip_bytes, _warnings = await BundleExporter().export(
                    pod_id=pod_id,
                    user_id=user_id,
                    with_data=False,
                    include=None,
                    ctx=ctx,
                    uow=uow,
                    on_progress=_noop_progress,
                )

        files = await run_blocking(_zip_to_files, zip_bytes, limiter="cpu_bound")
        counts = _resource_counts(files)
        pod_meta = _pod_meta_from_files(files)
        repo_description = pod_meta.get("description")

        # (2) PUBLISHING — create the repo + push files via Composio (no DB held
        # across the HTTP calls beyond each short per-operation scope).
        state.status = PublishStatus.PUBLISHING
        await store.save_publish(state)
        await publish_bundle_event(publish_id, status_payload(state.status.value, state.seq))

        async def _run_op(op_name: str, payload: dict) -> dict:
            async with uow_scope(worker_ctx.uow_factory) as op_uow:
                op_ctx = await AuthorizationDataService(
                    op_uow.session
                ).build_user_context(user_id=user_id, pod_id=pod_id)
                from app.modules.connectors.api.dependencies import (
                    build_connector_operation_service,
                )

                svc = build_connector_operation_service(op_uow)
                resp = await svc.execute_operation(
                    connector_id="github",
                    operation_name=op_name,
                    payload=payload,
                    user_id=user_id,
                    actor=op_ctx,
                    account_id=state.account_id,
                )
            return resp.model_dump() if hasattr(resp, "model_dump") else (
                resp if isinstance(resp, dict) else {}
            )

        publisher = GithubPublisher(ComposioGithubOps(_run_op))

        # Create the repo first so the README's install button carries the real
        # GitHub owner (not the repo name), then render + optionally AI-polish the
        # README and push everything.
        repo = await publisher.create_repo(
            repo_name=state.repo_name,
            private=state.private,
            description=repo_description,
        )
        state.repo_url = repo.html_url
        state.repo_created = True
        await store.save_publish(state)

        readme = render_readme(
            pod_name=pod_meta.get("name") or pod_name.removesuffix(".zip"),
            description=repo_description,
            resource_counts=counts,
            owner=repo.owner,
            repo=repo.repo,
            icon_url=pod_meta.get("icon_url"),
        )
        if state.ai_readme:
            polish_fn = build_system_polish_fn(
                user_id=user_id,
                organization_id=organization_id,
                pod_id=pod_id,
            )
            readme = await polish_readme(readme, polish_fn=polish_fn)
        state.readme = readme
        await store.save_publish(state)

        async def _on_file(path: str, done: int, total: int) -> None:
            state.progress.done = done
            state.progress.total = total
            await store.save_publish(state)
            await publish_bundle_event(
                publish_id, progress_payload(done, total, state.seq, path=path)
            )

        repo = await publisher.publish(
            repo_name=state.repo_name,
            private=state.private,
            description=repo_description,
            files=files,
            readme=readme,
            on_progress=_on_file,
            already_created=repo,
        )

        state.status = PublishStatus.COMPLETED
        state.repo_url = repo.html_url
        state.repo_created = True
        state.completed_at = _now()
        await store.save_publish(state)
        await publish_bundle_event(
            publish_id,
            completed_payload(state.status.value, state.seq, repo_url=repo.html_url),
        )
    except DomainError as exc:
        await _fail_publish(store, state, str(exc))
        logger.warning("Pod publish %s failed (terminal): %s", publish_id, exc)
    except Exception as exc:
        await _fail_publish(store, state, "Publish failed due to a transient error.")
        logger.error("Pod publish %s failed (retryable): %s", publish_id, exc)
        raise


async def _fail_publish(store, state: PublishState, message: str) -> None:
    state.status = PublishStatus.FAILED
    state.error = message
    state.completed_at = _now()
    try:
        await store.save_publish(state)
        await publish_bundle_event(state.publish_id, error_payload(message, state.seq))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist FAILED publish %s: %s", state.publish_id, exc)


def _zip_to_files(zip_bytes: bytes) -> dict[str, bytes]:
    """Unpack an exported bundle zip to a ``{path: bytes}`` map for upload,
    dropping directory entries and any pre-existing README (we render our own)."""
    import io
    import zipfile

    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.lower().endswith("readme.md"):
                continue
            files[name] = zf.read(info)
    return files


def _pod_meta_from_files(files: dict[str, bytes]) -> dict[str, str | None]:
    """Read the pod's name/description/icon_url from the bundle's ``pod.json`` so
    the README uses the real pod identity (not the export filename)."""
    import json

    raw = files.get("pod.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - a malformed manifest just yields no meta
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "name": data.get("name"),
        "description": data.get("description"),
        "icon_url": data.get("icon_url"),
    }


def _resource_counts(files: dict[str, bytes]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in files:
        parts = path.split("/")
        if len(parts) >= 2:
            counts[parts[0]] = counts.get(parts[0], 0)
    # Count distinct resource directories per type.
    seen: dict[str, set[str]] = {}
    for path in files:
        parts = path.split("/")
        if len(parts) >= 2:
            seen.setdefault(parts[0], set()).add(parts[1])
    return {k: len(v) for k, v in seen.items()}


async def _resolve_importer_pod_member_id(
    worker_ctx: AppWorkerContext, *, pod_id: UUID, user_id: UUID
) -> str | None:
    """The importing user's own pod-member id in the target pod — what a
    ``pod_member`` (``${..._assignee}``) variable resolves to when the importer
    doesn't supply one explicitly, matching the CLI's assignee resolution.

    Best-effort: returns ``None`` (leaving the placeholder unresolved, so the
    service drops the assignee) if the user has no membership or the lookup
    fails, rather than failing the whole apply over one workflow assignee."""
    from app.modules.pod.api.dependencies import get_pod_member_service

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
    except Exception as exc:  # noqa: BLE001 — assignee auto-resolution is best-effort
        logger.warning(
            "Could not resolve importer pod-member id for pod %s user %s (%s); "
            "workflow assignees left unresolved",
            pod_id,
            user_id,
            exc,
        )
        return None


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


# A non-terminal job untouched for longer than this is presumed dead (worker
# crash/restart): the apply job's own timeout is 1800s, so ~40min leaves a wide
# margin before the sweep intervenes.
_STUCK_AFTER_SECONDS = 40 * 60


@streaq_cron("*/30 * * * *", name="sweep_pod_bundle_staging")
async def sweep_pod_bundle_staging() -> None:
    """Reclaim staged archives whose ephemeral state has expired, and mark
    crashed (long-idle, non-terminal) imports/exports FAILED so the UI stops
    showing them as in-progress. Keyed off the object-store inventory, so it is
    bounded by how many archives are actually staged."""
    reclaimed, recovered = await _sweep(
        get_pod_bundle_state_store(), BundleStagingStorage()
    )
    if reclaimed or recovered:
        logger.info(
            "Pod bundle sweep: reclaimed %d orphaned archives, recovered %d stuck jobs",
            reclaimed,
            recovered,
        )


async def _sweep(store, staging) -> tuple[int, int]:
    # Per-kind retention is driven by the state TTL, not this cron: a READY
    # export is written with the export TTL (default 24h) while imports use the
    # default ~6h, so an export's state (and thus its archive, reclaimed only
    # once the state is gone) naturally outlives an import's.
    from datetime import timedelta

    cutoff = _now() - timedelta(seconds=_STUCK_AFTER_SECONDS)

    reclaimed = 0
    recovered = 0
    for kind, get_state, save_state, is_import in (
        ("pod-imports", store.get_import, store.save_import, True),
        ("pod-exports", store.get_export, store.save_export, False),
    ):
        try:
            archives = await staging.list_archives(kind)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sweep: could not list %s: %s", kind, exc)
            continue
        for job_id, _ in archives:
            state = await get_state(job_id)
            if state is None:
                # State TTL expired → the job is unreferenceable; reclaim bytes.
                try:
                    await staging.delete_archive(kind, job_id)  # type: ignore[arg-type]
                    reclaimed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Sweep: delete %s/%s failed: %s", kind, job_id, exc)
                continue
            if not state.is_terminal and state.updated_at < cutoff:
                if is_import:
                    state.status = ImportStatus.FAILED
                else:
                    state.status = ExportStatus.FAILED
                state.error = "Interrupted (worker restart or crash); start over."
                state.completed_at = _now()
                await save_state(state)  # type: ignore[arg-type]
                await publish_bundle_event(
                    job_id, error_payload(state.error, state.seq)
                )
                recovered += 1

    return reclaimed, recovered
