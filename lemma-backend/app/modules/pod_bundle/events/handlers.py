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
from app.modules.pod_bundle.domain.state import ExportState, ExportStatus
from app.modules.pod_bundle.infrastructure.exporter import BundleExporter
from app.modules.pod_bundle.infrastructure.realtime import (
    completed_payload,
    error_payload,
    progress_payload,
    publish_bundle_event,
    status_payload,
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


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
