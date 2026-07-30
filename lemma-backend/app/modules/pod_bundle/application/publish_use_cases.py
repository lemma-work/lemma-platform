"""Application/use-case layer for publishing a pod to GitHub.

``start_publish`` authorizes, acquires an account/repository concurrency lock,
writes the initial ``QUEUED`` state, and enqueues the ``publish_pod_github`` job.
``get_publish`` is a state read. All heavy work — export, README, Composio
uploads — happens in the worker with short per-operation UoW scopes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
)
from app.modules.pod_bundle.domain.state import PublishMode, PublishState, PublishStatus
from app.modules.pod_bundle.infrastructure.publish_lock import (
    PublishConcurrencyLock,
    get_publish_concurrency_lock,
)
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PUBLISH_JOB_NAME = "publish_pod_github"


def publish_job_id(publish_id: UUID) -> str:
    return f"pod-publish:{publish_id}"


class PublishUseCases:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        state_store: PodBundleStateStore | None = None,
        job_queue=None,
        publish_lock: PublishConcurrencyLock | None = None,
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._job_queue = job_queue or get_streaq_job_queue()
        self._publish_lock = publish_lock or get_publish_concurrency_lock()

    async def start_publish(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        repo_name: str,
        private: bool,
        account_id: UUID,
        ai_readme: bool,
        mode: PublishMode = PublishMode.CREATE,
    ) -> PublishState:
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        publish_id = uuid4()
        state = PublishState(
            publish_id=publish_id,
            pod_id=pod_id,
            user_id=user_id,
            status=PublishStatus.QUEUED,
            repo_name=repo_name,
            mode=mode,
            private=private,
            account_id=account_id,
            ai_readme=ai_readme,
        )
        acquired = await self._publish_lock.acquire(
            account_id=account_id,
            repo_name=repo_name,
            owner=publish_id,
        )
        if not acquired:
            raise BundleJobConflictError(
                "A publish for this GitHub account and repository is already running."
            )
        try:
            await self._state_store.save_publish(state)
            job = await self._job_queue.enqueue(
                PUBLISH_JOB_NAME,
                context={
                    "publish_id": str(publish_id),
                    "pod_id": str(pod_id),
                    "user_id": str(user_id),
                },
                _job_id=publish_job_id(publish_id),
            )
            if job is None:
                raise BundleJobConflictError("The publish job could not be queued.")
        except Exception:
            await self._publish_lock.release(
                account_id=account_id,
                repo_name=repo_name,
                owner=publish_id,
            )
            raise
        return state

    async def get_publish(
        self, *, pod_id: UUID, publish_id: UUID, user_id: UUID
    ) -> PublishState:
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_READ
        )
        state = await self._state_store.get_publish(publish_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        return state

    async def _authorize(
        self, *, pod_id: UUID, user_id: UUID, action: str
    ) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
