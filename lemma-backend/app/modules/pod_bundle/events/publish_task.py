"""Durable GitHub publish worker and its resumable phase collaborators."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.concurrency.offload import run_blocking
from app.core.domain.errors import DomainError
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import BundleStagingMissingError
from app.modules.pod_bundle.domain.state import (
    PublishFileProgress,
    PublishState,
    PublishStatus,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.ai_readme import (
    build_system_polish_fn,
    polish_readme,
)
from app.modules.pod_bundle.infrastructure.exporter import BundleExporter
from app.modules.pod_bundle.infrastructure.github_publisher import (
    ComposioGithubOps,
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.infrastructure.readme import render_readme
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _mark_exporting(store, state: PublishState) -> None:
    retrying = state.status is PublishStatus.FAILED
    state.status = PublishStatus.EXPORTING
    state.error = None
    state.error_type = None
    state.error_code = None
    state.completed_at = None
    if retrying:
        state.attempt += 1
        await store.reopen_publish(state)
    else:
        await store.save_publish(state)


async def _load_or_export_archive(
    *,
    worker_ctx: AppWorkerContext,
    store,
    staging: BundleStagingStorage,
    state: PublishState,
    publish_id: UUID,
    pod_id: UUID,
    user_id: UUID,
) -> tuple[str, bytes, UUID | None]:
    archive = (
        await staging.get_archive("pod-publishes", publish_id)
        if state.staging_key
        else None
    )
    if archive is not None:
        return "pod.zip", archive, None
    if state.files:
        raise BundleStagingMissingError(
            "The durable publish archive expired before completion."
        )

    async def _noop_progress(done: int, total: int) -> None:
        del done, total

    async with uow_scope(worker_ctx.uow_factory) as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        organization_id = ctx.organization_id
        async with context_scope(ctx):
            pod_name, archive, _warnings = await BundleExporter().export(
                pod_id=pod_id,
                user_id=user_id,
                with_data=False,
                include=None,
                ctx=ctx,
                uow=uow,
                on_progress=_noop_progress,
            )
    state.staging_key = await staging.put_archive("pod-publishes", publish_id, archive)
    await store.save_publish(state)
    return pod_name, archive, organization_id


def _operation_runner(
    *,
    worker_ctx: AppWorkerContext,
    state: PublishState,
    pod_id: UUID,
    user_id: UUID,
):
    async def run(operation_name: str, payload: dict) -> dict:
        async with uow_scope(worker_ctx.uow_factory) as uow:
            actor = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            from app.composition.pod_bundle_resources import (
                build_connector_operation_service,
            )

            service = build_connector_operation_service(uow)
            response = await service.execute_operation(
                connector_id="github",
                operation_name=operation_name,
                payload=payload,
                user_id=user_id,
                actor=actor,
                account_id=state.account_id,
            )
        if hasattr(response, "model_dump"):
            return response.model_dump()
        return response if isinstance(response, dict) else {}

    return run


def _persisted_repo(state: PublishState) -> RepoCreateResult | None:
    if not (
        state.repo_created and state.repo_owner and state.repo_slug and state.repo_url
    ):
        return None
    return RepoCreateResult(
        owner=state.repo_owner,
        repo=state.repo_slug,
        html_url=state.repo_url,
    )


async def _ensure_repo(
    *,
    store,
    state: PublishState,
    publisher: GithubPublisher,
    description: str | None,
) -> RepoCreateResult:
    repo = _persisted_repo(state) or await publisher.create_repo(
        repo_name=state.repo_name,
        private=state.private,
        description=description,
    )
    state.repo_url = repo.html_url
    state.repo_created = True
    state.repo_owner = repo.owner
    state.repo_slug = repo.repo
    await store.save_publish(state)
    return repo


async def _ensure_readme(
    *,
    worker_ctx: AppWorkerContext,
    state: PublishState,
    user_id: UUID,
    pod_id: UUID,
    organization_id: UUID | None,
    pod_name: str,
    pod_meta: dict[str, str | None],
    description: str | None,
    counts: dict[str, int],
    repo: RepoCreateResult,
) -> str:
    if state.readme is not None:
        return state.readme
    readme = render_readme(
        pod_name=pod_meta.get("name") or pod_name.removesuffix(".zip"),
        description=description,
        resource_counts=counts,
        owner=repo.owner,
        repo=repo.repo,
        icon_url=pod_meta.get("icon_url"),
    )
    if state.ai_readme:
        if organization_id is None:
            async with uow_scope(worker_ctx.uow_factory) as uow:
                ctx = await AuthorizationDataService(uow.session).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                organization_id = ctx.organization_id
        polish = build_system_polish_fn(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
        )
        readme = await polish_readme(readme, polish_fn=polish)
    state.readme = readme
    return readme


def _initialize_file_progress(
    state: PublishState,
    files: dict[str, bytes],
) -> dict[str, PublishFileProgress]:
    progress = {item.path: item for item in state.files}
    for path in ["README.md", *files]:
        if path not in progress:
            item = PublishFileProgress(path=path)
            state.files.append(item)
            progress[path] = item
    state.progress.total = len(progress)
    state.progress.done = sum(item.status is StepStatus.DONE for item in state.files)
    return progress


async def _publish_files(
    *,
    store,
    state: PublishState,
    publish_id: UUID,
    publisher: GithubPublisher,
    repo: RepoCreateResult,
    description: str | None,
    files: dict[str, bytes],
    readme: str,
) -> RepoCreateResult:
    progress = _initialize_file_progress(state, files)
    await store.save_publish(state)

    async def on_file(path: str, done: int, total: int) -> None:
        item = progress[path]
        item.status = StepStatus.DONE
        item.error = None
        state.progress.done = done
        state.progress.total = total
        await store.save_publish(state)
        await publish_bundle_event(
            publish_id, progress_payload(done, total, state.seq, path=path)
        )

    return await publisher.publish(
        repo_name=state.repo_name,
        private=state.private,
        description=description,
        files=files,
        readme=readme,
        on_progress=on_file,
        already_created=repo,
        completed_paths={
            item.path for item in state.files if item.status is StepStatus.DONE
        },
    )


@streaq_task(name="publish_pod_github")
async def publish_pod_github(context: dict[str, str | None]) -> None:
    worker_ctx: AppWorkerContext = streaq_worker.context
    publish_id = UUID(str(context["publish_id"]))
    pod_id = UUID(str(context["pod_id"]))
    user_id = UUID(str(context["user_id"]))
    store = get_pod_bundle_state_store()
    state = await store.get_publish(publish_id)
    if state is None or state.status is PublishStatus.COMPLETED:
        return

    try:
        await _mark_exporting(store, state)
        await publish_bundle_event(
            publish_id, status_payload(state.status.value, state.seq)
        )
        staging = BundleStagingStorage()
        pod_name, archive, organization_id = await _load_or_export_archive(
            worker_ctx=worker_ctx,
            store=store,
            staging=staging,
            state=state,
            publish_id=publish_id,
            pod_id=pod_id,
            user_id=user_id,
        )
        files = await run_blocking(_zip_to_files, archive, limiter="cpu_bound")
        counts = _resource_counts(files)
        pod_meta = _pod_meta_from_files(files)
        description = pod_meta.get("description")

        state.status = PublishStatus.PUBLISHING
        await store.save_publish(state)
        await publish_bundle_event(
            publish_id, status_payload(state.status.value, state.seq)
        )
        publisher = GithubPublisher(
            ComposioGithubOps(
                _operation_runner(
                    worker_ctx=worker_ctx,
                    state=state,
                    pod_id=pod_id,
                    user_id=user_id,
                )
            )
        )
        repo = await _ensure_repo(
            store=store,
            state=state,
            publisher=publisher,
            description=description,
        )
        readme = await _ensure_readme(
            worker_ctx=worker_ctx,
            state=state,
            user_id=user_id,
            pod_id=pod_id,
            organization_id=organization_id,
            pod_name=pod_name,
            pod_meta=pod_meta,
            description=description,
            counts=counts,
            repo=repo,
        )
        repo = await _publish_files(
            store=store,
            state=state,
            publish_id=publish_id,
            publisher=publisher,
            repo=repo,
            description=description,
            files=files,
            readme=readme,
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
        logger.warning(
            "pod_bundle.publish_task.pod_publish_s_terminal_s.degraded",
            publish_id=publish_id,
        )
    except Exception:
        await _fail_publish(store, state, "Publish failed due to a transient error.")
        logger.debug(
            'pod_bundle.publish_task.pod_publish_s_retryable_s.propagated',
            publish_id=publish_id,
        exc_info=True,
    )
        raise


async def _fail_publish(store, state: PublishState, message: str) -> None:
    state.status = PublishStatus.FAILED
    state.error = message
    state.completed_at = _now()
    try:
        await store.save_publish(state)
        await publish_bundle_event(state.publish_id, error_payload(message, state.seq))
    except Exception:
        logger.debug(
            'pod_bundle.publish_task.persist_publish_s_s.diagnostic',
            publish_id=state.publish_id,
        )


def _zip_to_files(archive: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for info in bundle.infolist():
            if info.is_dir() or info.filename.lower().endswith("readme.md"):
                continue
            files[info.filename] = bundle.read(info)
    return files


def _pod_meta_from_files(files: dict[str, bytes]) -> dict[str, str | None]:
    raw = files.get("pod.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "name": data.get("name"),
        "description": data.get("description"),
        "icon_url": data.get("icon_url"),
    }


def _resource_counts(files: dict[str, bytes]) -> dict[str, int]:
    resources: dict[str, set[str]] = {}
    for path in files:
        parts = path.split("/")
        if len(parts) >= 2:
            resources.setdefault(parts[0], set()).add(parts[1])
    return {kind: len(names) for kind, names in resources.items()}
