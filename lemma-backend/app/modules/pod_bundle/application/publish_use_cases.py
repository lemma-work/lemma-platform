"""Application/use-case layer for publishing a pod to GitHub.

``start_publish`` authorizes, writes the initial ``QUEUED`` state, and enqueues
the ``publish_pod_github`` job (its dedup id doubles as the concurrency guard);
``get_publish`` is a pure Redis read. All heavy work — export, README, Composio
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
from app.modules.pod_bundle.domain.state import PublishState, PublishStatus
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
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._job_queue = job_queue or get_streaq_job_queue()

    async def start_publish(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        repo_name: str,
        private: bool,
        account_id: UUID | None,
        ai_readme: bool,
    ) -> PublishState:
        await self._authorize(pod_id=pod_id, user_id=user_id)
        publish_id = uuid4()
        state = PublishState(
            publish_id=publish_id,
            pod_id=pod_id,
            user_id=user_id,
            status=PublishStatus.QUEUED,
            repo_name=repo_name,
            private=private,
            account_id=account_id,
            ai_readme=ai_readme,
        )
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
            raise BundleJobConflictError("A publish for this pod is already running.")
        return state

    async def get_publish(
        self, *, pod_id: UUID, publish_id: UUID, user_id: UUID
    ) -> PublishState:
        await self._authorize(pod_id=pod_id, user_id=user_id)
        state = await self._state_store.get_publish(publish_id)
        if state is None or state.pod_id != pod_id:
            raise BundleJobExpiredError()
        return state

    async def _authorize(self, *, pod_id: UUID, user_id: UUID) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(Permissions.POD_READ)
