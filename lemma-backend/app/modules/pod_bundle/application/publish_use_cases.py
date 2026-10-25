"""Application/use-case layer for publishing a pod to GitHub.

``start_publish`` authorizes, resolves the pod's `github` connector for the
person publishing (so a missing connection is refused at the request, with its
connect link), acquires the account/repository concurrency lock, and writes the
initial ``QUEUED`` state with its ``pod_bundle.publish`` work item in one
transaction.
``get_publish`` is a state read. All heavy work — export, README, GitHub
uploads — happens in the worker with short per-operation UoW scopes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
)
from app.modules.pod_bundle.domain.state import PublishMode, PublishState, PublishStatus
from app.modules.pod_bundle.events.work import (
    PUBLISH,
    BundleJobQueue,
    PublishJob,
    carried_origin,
    publish_key,
)
from app.modules.pod_bundle.infrastructure.publish_lock import (
    PublishConcurrencyLock,
    get_publish_concurrency_lock,
    publish_lock_key,
)
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

PUBLISH_JOB_NAME = PUBLISH


def publish_job_id(publish_id: UUID) -> str:
    return publish_key(publish_id)


class PublishUseCases:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        state_store: PodBundleStateStore | None = None,
        job_queue: BundleJobQueue | None = None,
        publish_lock: PublishConcurrencyLock | None = None,
        github_account: Callable[..., Awaitable[UUID]] | None = None,
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._job_queue = job_queue or BundleJobQueue()
        self._publish_lock = publish_lock or get_publish_concurrency_lock()
        # A collaborator so the use case's own decisions -- refuse before
        # queueing, lock on what resolved -- run without a database.
        self._github_account = github_account or self._resolve_github_account

    async def start_publish(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        repo_name: str,
        private: bool,
        ai_readme: bool,
        mode: PublishMode = PublishMode.CREATE,
    ) -> PublishState:
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        # Raises the connector's typed refusal (CONNECTION_REQUIRED with its
        # connect link, POD_CONNECTOR_NOT_FOUND, ...) before anything is queued.
        account_id = await self._github_account(pod_id=pod_id, user_id=user_id)
        lock_key = publish_lock_key(account_id, repo_name)
        publish_id = uuid4()
        state = PublishState(
            publish_id=publish_id,
            pod_id=pod_id,
            user_id=user_id,
            status=PublishStatus.QUEUED,
            repo_name=repo_name,
            mode=mode,
            private=private,
            publish_lock_key=lock_key,
            ai_readme=ai_readme,
        )
        acquired = await self._publish_lock.acquire(key=lock_key, owner=publish_id)
        if not acquired:
            raise BundleJobConflictError(
                "A publish for this GitHub account and repository is already running."
            )
        try:
            async with self._uow_factory() as uow:
                await self._state_store.stage_publish(uow, state)
                queued = await self._job_queue.queue_job(
                    uow,
                    PUBLISH,
                    PublishJob(
                        publish_id=publish_id,
                        pod_id=pod_id,
                        user_id=user_id,
                        **carried_origin(),
                    ),
                    pod_id=pod_id,
                    dedup_key=publish_key(publish_id),
                )
                if not queued:
                    raise BundleJobConflictError("The publish job could not be queued.")
                await uow.commit()
        except Exception:
            await self._publish_lock.release(key=lock_key, owner=publish_id)
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

    async def _resolve_github_account(self, *, pod_id: UUID, user_id: UUID) -> UUID:
        """The account the pod's `github` connector runs as for this person.

        Resolved as the person, in a short scope of its own; the worker
        resolves again per GitHub call, under the same person.
        """
        from app.modules.connectors.contracts.pod_connectors import (
            resolve_pod_connector,
        )

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                resolved = await resolve_pod_connector(
                    uow, ctx, pod_id=pod_id, slug="github"
                )
            await uow.commit()
        return resolved.account_id

    async def _authorize(self, *, pod_id: UUID, user_id: UUID, action: str) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)
