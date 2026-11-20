"""Installing several plugins into one Lemma, in one request.

A plugin is a bundle in a folder of a GitHub repository (``install/mail``).
One request names several; the server works through them in that order with
the install page's defaults, whether or not anybody is still watching, and
stops on any one where the page would have asked a question.

**Nothing new is stored.** Each bundle is an ordinary import -- its own job
row, plan, steps and status endpoint -- whose state carries its place in the
install (``ImportState.install``). Starting the install writes every import
``QUEUED`` and one ``pod_bundle.install_plugin`` item per bundle in a single
transaction; the pod's bundle jobs run one at a time, and each item is a few
milliseconds behind the one before, so they run in the order named. Each item
fetches, plans, approves and applies its bundle (``events/plugin_install``),
under the plan key its import would have had anyway, so cancelling the import,
the stale-job sweep and a dead item treat it as any import.

**A bundle that stops does not stop the others.** One that would ask a
question is withdrawn before anything is applied, with the question kept on
it; one that fails keeps its steps. Retrying the install puts each failed
bundle back in the queue: one with a plan carries on from the step that
failed -- the same import applied again, never a fresh one that would find
its half-made parts and keep them as they are -- and one that failed before
its plan was made starts over, since nothing of it was applied.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import context_scope, uow_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobNotFoundError,
)
from app.modules.pod_bundle.domain.plugin_install import PluginInstallState, outcome_of
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportState,
    ImportStatus,
    InstallSlot,
)
from app.modules.pod_bundle.events.work import (
    INSTALL_PLUGIN,
    BundleJobQueue,
    GithubImportJob,
    plan_key,
)
from app.modules.pod_bundle.infrastructure.install_reads import imports_of_install
from app.modules.pod_bundle.infrastructure.rate_limiter import (
    BundleRateLimiter,
    get_bundle_rate_limiter,
)
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)


@dataclass(frozen=True, slots=True)
class PluginSource:
    """Where one plugin's bundle is: a repository, and the folder in it."""

    owner: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    path: str | None = None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Install:
    install_id: UUID
    pod_id: UUID
    #: The install's imports, in the order the request named them.
    imports: list[ImportState]


class InstallReader(Protocol):
    """Reads an install's imports back (``install_reads.imports_of_install``)."""

    def __call__(
        self, *, pod_id: UUID, install_id: UUID
    ) -> Awaitable[list[ImportState]]: ...


class Authorize(Protocol):
    """Refuses unless the person may do ``action`` in the pod (``PodAuthority``)."""

    def __call__(
        self, *, pod_id: UUID, user_id: UUID, action: str
    ) -> Awaitable[None]: ...


def install_delay(position: int) -> timedelta:
    """How far behind the install's first item this one is queued."""
    return timedelta(milliseconds=position)


class InstallUseCases:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        state_store: PodBundleStateStore | None = None,
        job_queue: BundleJobQueue | None = None,
        rate_limiter: BundleRateLimiter | None = None,
        read_install: InstallReader = imports_of_install,
        authorize: Authorize | None = None,
    ):
        self._uow_factory = uow_factory
        self._state_store = state_store or get_pod_bundle_state_store()
        self._job_queue = job_queue or BundleJobQueue()
        self._rate_limiter = rate_limiter or get_bundle_rate_limiter()
        self._read_install = read_install
        self._authorize = authorize or PodAuthority(uow_factory)

    async def start_install(
        self, *, pod_id: UUID, user_id: UUID, sources: list[PluginSource]
    ) -> Install:
        """Queue every bundle, in order, and return before any is fetched."""
        from app.modules.pod_bundle.infrastructure.github_fetcher import (
            parse_repo_ref,
        )

        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        located = [
            (
                source,
                parse_repo_ref(
                    repo_url=source.repo_url, owner=source.owner, repo=source.repo
                ),
            )
            for source in sources
        ]
        # Each bundle is an import, and counts as one, as it did when the
        # browser started them one by one.
        for _ in located:
            await self._rate_limiter.check_and_increment(
                user_id=user_id,
                operation="import",
                limit=pod_bundle_settings.pod_bundle_daily_import_limit,
            )
        install_id = uuid4()
        imports: list[ImportState] = []
        async with self._uow_factory() as uow:
            for position, (source, (owner, repo)) in enumerate(located):
                state = ImportState(
                    import_id=uuid4(),
                    pod_id=pod_id,
                    user_id=user_id,
                    status=ImportStatus.QUEUED,
                    source=BundleSource(
                        kind=BundleSourceKind.GITHUB,
                        repo_url=source.repo_url
                        or f"https://github.com/{owner}/{repo}",
                        path=source.path,
                        ref=source.ref,
                    ),
                    install=InstallSlot(install_id=install_id, position=position),
                )
                await self._state_store.stage_import(uow, state)
                await self._queue(uow, state, owner=owner, repo=repo)
                imports.append(state)
            await uow.commit()
        return Install(install_id=install_id, pod_id=pod_id, imports=imports)

    async def get_install(
        self, *, pod_id: UUID, install_id: UUID, user_id: UUID
    ) -> Install:
        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_READ
        )
        return await self._starters_install(
            pod_id=pod_id, install_id=install_id, user_id=user_id
        )

    async def retry_install(
        self, *, pod_id: UUID, install_id: UUID, user_id: UUID
    ) -> Install:
        """Put each failed bundle back in the queue, where it stopped.

        With a plan, it waits to be applied again: the worker approves it
        with the page's defaults as before and applies it, and steps already
        applied stay applied. Without one, nothing of it was applied, so it
        starts over from the repository. Every other bundle is left as it is.
        """
        from app.modules.pod_bundle.infrastructure.github_fetcher import (
            parse_repo_ref,
        )

        await self._authorize(
            pod_id=pod_id, user_id=user_id, action=Permissions.POD_UPDATE
        )
        install = await self._starters_install(
            pod_id=pod_id, install_id=install_id, user_id=user_id
        )
        failed = [
            s for s in install.imports if outcome_of(s) is PluginInstallState.FAILED
        ]
        if not failed:
            return install
        async with self._uow_factory() as uow:
            for state in failed:
                _reopen(state)
                await self._state_store.stage_import(uow, state, reopen=True)
                owner, repo = parse_repo_ref(
                    repo_url=state.source.repo_url, owner=None, repo=None
                )
                await self._queue(uow, state, owner=owner, repo=repo)
            await uow.commit()
        return install

    async def _queue(
        self, uow: SqlAlchemyUnitOfWork, state: ImportState, *, owner: str, repo: str
    ) -> None:
        assert state.install is not None, "an install's import"
        queued = await self._job_queue.queue_job(
            uow,
            INSTALL_PLUGIN,
            GithubImportJob(
                import_id=state.import_id,
                pod_id=state.pod_id,
                user_id=state.user_id,
                owner=owner,
                repo=repo,
            ),
            pod_id=state.pod_id,
            dedup_key=plan_key(state.import_id),
            delay=install_delay(state.install.position),
        )
        if not queued:
            raise BundleJobConflictError("This plugin is already being installed.")

    async def _starters_install(
        self, *, pod_id: UUID, install_id: UUID, user_id: UUID
    ) -> Install:
        """The install, when ``user_id`` started it; 404 otherwise.

        Its imports were planned with the starter's authority and list their
        own accounts, so it is theirs alone, as each of its imports is.
        """
        imports = await self._read_install(pod_id=pod_id, install_id=install_id)
        if not imports or any(state.user_id != user_id for state in imports):
            raise BundleJobNotFoundError("No such install in this pod.")
        return Install(install_id=install_id, pod_id=pod_id, imports=imports)


class PodAuthority:
    """Whether the person may do ``action`` in the pod, as their request asks."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    async def __call__(self, *, pod_id: UUID, user_id: UUID, action: str) -> None:
        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            async with context_scope(ctx):
                await ctx.require(action)


def _reopen(state: ImportState) -> None:
    """A failed import of the install, made ready to go through the queue again."""
    state.attempt += 1
    state.status = (
        ImportStatus.QUEUED
        if state.plan is None
        else ImportStatus.AWAITING_CONFIRMATION
    )
    state.error = None
    state.error_type = None
    state.error_code = None
    state.connect_url = None
    state.retryable = False
    state.current_step = None
    state.completed_at = None


__all__ = ["Install", "InstallUseCases", "PluginSource", "install_delay"]
