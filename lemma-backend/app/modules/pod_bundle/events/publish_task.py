"""Durable GitHub publish worker and its resumable phase collaborators."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import UUID

from streaq import StreaqRetry

from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.concurrency.offload import run_blocking
from app.core.domain.errors import DomainError
from app.core.infrastructure.jobs.streaq_runtime import (
    Lane,
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
    NativeGithubOps,
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.infrastructure.readme import render_readme
from app.modules.pod_bundle.infrastructure.publish_lock import (
    get_publish_concurrency_lock,
)
from app.modules.pod_bundle.infrastructure.social_card import render_social_card
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


async def _release_publish_lock(state: PublishState) -> None:
    # ``account_id`` was optional in pre-0.6.8 job snapshots. New jobs always
    # have one, while a rolling worker can still finish an older snapshot.
    if state.account_id is None:
        return
    await get_publish_concurrency_lock().release(
        account_id=state.account_id,
        repo_name=state.repo_name,
        owner=state.publish_id,
    )


async def _mark_exporting(store, state: PublishState) -> None:
    reopening = state.status is PublishStatus.FAILED
    retrying = state.retryable or state.error is not None
    if retrying:
        state.attempt += 1
    state.status = PublishStatus.EXPORTING
    state.error = None
    state.error_type = None
    state.error_code = None
    state.retryable = False
    state.completed_at = None
    if reopening:
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
            pod_name, archive, warnings = await BundleExporter().export(
                pod_id=pod_id,
                user_id=user_id,
                # A published repository is the pod's shape, never its
                # contents: no table named for seeding, no folder named for
                # export.
                data_tables=None,
                file_folders=None,
                include=None,
                ctx=ctx,
                uow=uow,
                on_progress=_noop_progress,
            )
    state.warnings = warnings
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
        from app.composition.pod_bundle_resources import (
            build_connector_operation_service,
        )

        # Phase 1 (short scope): authorize and resolve the execution plan,
        # including credentials. Exiting the scope commits any OAuth-token
        # refresh and returns the connection to the pool.
        async with uow_scope(worker_ctx.uow_factory) as uow:
            actor = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            resolved = await build_connector_operation_service(uow).resolve_execution(
                connector_id="github",
                operation_name=operation_name,
                payload=payload,
                user_id=user_id,
                actor=actor,
                account_id=state.account_id,
            )

        # Phase 2: the GitHub round trip, with NO pooled connection held. This
        # matters more here than at most call sites: ``_create_blobs`` runs up
        # to ``_BLOB_CONCURRENCY`` of these at once, so holding the connection
        # across the call meant one publish could occupy eight connections of a
        # pool of ten for the length of eight HTTP requests. ``execute_resolved``
        # issues no DB I/O, so the short scope below never checks a connection
        # out across the call -- it only supplies the service collaborator.
        async with uow_scope(worker_ctx.uow_factory) as uow:
            response = await build_connector_operation_service(uow).execute_resolved(
                resolved
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
        default_branch=state.repo_branch or "main",
        private=state.private,
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
        mode=state.mode,
    )
    state.repo_url = repo.html_url
    state.repo_created = True
    state.repo_owner = repo.owner
    state.repo_slug = repo.repo
    state.repo_branch = repo.default_branch
    if repo.private is not None:
        state.private = repo.private
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
        publish_id=str(publish_id),
        mode=state.mode,
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


@streaq_task(name="publish_pod_github", lane=Lane.BULK)
async def publish_pod_github(context: dict[str, str | None]) -> None:
    worker_ctx: AppWorkerContext = streaq_worker.context
    publish_id = UUID(str(context["publish_id"]))
    pod_id = UUID(str(context["pod_id"]))
    user_id = UUID(str(context["user_id"]))
    store = get_pod_bundle_state_store()
    state = await store.get_publish(publish_id)
    if state is None:
        return
    if state.is_terminal:
        await _release_publish_lock(state)
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
            NativeGithubOps(
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
        display_name = pod_meta.get("name") or pod_name.removesuffix(".zip")
        files["social-card.png"] = await run_blocking(
            render_social_card,
            pod_name=display_name,
            source_label=f"github.com/{repo.owner}/{repo.repo}",
            limiter="cpu_bound",
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
        await _release_publish_lock(state)
    except DomainError as exc:
        if _is_retryable_publish_error(exc) and state.attempt < 3:
            await _record_publish_retry(store, state, exc)
            raise StreaqRetry(delay=min(state.attempt**2, 9)) from exc
        await _fail_publish(store, state, exc)
        await _release_publish_lock(state)
        logger.warning(
            "pod_bundle.publish_task.pod_publish_s_terminal_s.degraded",
            publish_id=publish_id,
        )
    except Exception as exc:
        if state.attempt < 3:
            await _record_publish_retry(store, state, exc)
            raise StreaqRetry(delay=min(state.attempt**2, 9)) from exc
        await _fail_publish(
            store,
            state,
            exc,
            public_message="Publish failed after three transient attempts.",
        )
        await _release_publish_lock(state)
        logger.debug(
            "pod_bundle.publish_task.pod_publish_s_retryable_s.propagated",
            publish_id=publish_id,
            exc_info=True,
        )


def _is_retryable_publish_error(exc: DomainError) -> bool:
    if getattr(exc, "code", None) == "GITHUB_PUBLISH_CAPABILITY_UNAVAILABLE":
        return False
    status_code = getattr(exc, "status_code", None)
    return status_code in {408, 429} or (
        isinstance(status_code, int) and status_code >= 500
    )


async def _record_publish_retry(
    store,
    state: PublishState,
    exc: Exception,
) -> None:
    state.error = "GitHub is temporarily unavailable; retrying publish."
    state.error_type = type(exc).__name__
    state.error_code = str(getattr(exc, "code", None) or "POD_BUNDLE_GITHUB_TRANSIENT")
    state.retryable = True
    await store.save_publish(state)


async def _fail_publish(
    store,
    state: PublishState,
    exc: Exception,
    *,
    public_message: str | None = None,
) -> None:
    state.status = PublishStatus.FAILED
    state.error = public_message or str(exc)
    state.error_type = type(exc).__name__
    state.error_code = str(getattr(exc, "code", None) or "POD_BUNDLE_PUBLISH_FAILED")
    state.retryable = False
    state.completed_at = _now()
    # The last-resort reporter for a publish: if it is silent, a failed publish
    # looks permanently stuck to whoever is watching.
    try:
        await store.save_publish(state)
        await publish_bundle_event(
            state.publish_id,
            error_payload(state.error, state.seq),
        )
    except Exception:
        logger.error(
            "pod_bundle.publish_task.publish_failure_report.failed",
            publish_id=state.publish_id,
            exc_info=True,
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
