"""The app a bundle's pod opens on, set on the pod it is imported into.

``pod.json -> home_app`` names one of the bundle's apps by its name (its
``apps/<name>`` directory). Without it a pod opens on its oldest app, and an
import creates a bundle's apps in name order, so a plugin whose bases come
along with it opened on whichever base sorted first.

What a pod already has is kept, as the rest of an import keeps it: the
bundle's choice is set only on a pod that has none and had no apps before this
import. A pod that already opens somewhere -- by a person's choice, or because
it had apps whose oldest it opens on -- goes on opening there, and the import
says so; a person moves it from the pod's apps. A choice naming an app the pod
no longer has is no choice: the pod opens on its first app, as it would.

Set as the person importing, through the pod's own update, once every step has
landed, and only if the choice is still the one read: a person who chooses
while the import runs keeps theirs. A write the pod refuses (the person lost
``pod.update`` meanwhile, say), or one a database error such as a deadlock
stops, is a warning, not a failed import: the steps before it have landed. The
database being unavailable is not the work failing, and goes to the work item,
which retries the apply; its landed steps are skipped and its warnings said
once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import POD_MANIFEST_FILE
from sqlalchemy.exc import SQLAlchemyError

from app.core.concurrency.offload import run_blocking
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.state import (
    ImportState,
    StepAction,
    StepKind,
    StepStatus,
)

logger = get_logger(__name__)


def read_bundle_home_app(bundle_root: Path) -> str | None:
    """``pod.json -> home_app`` as written, or ``None`` when there is none."""
    pod_path = bundle_root / POD_MANIFEST_FILE
    if not pod_path.is_file():
        return None
    try:
        manifest = loads_jsonc(pod_path.read_text(encoding="utf-8"))
    except ValueError:
        return None  # the plan already refused a pod.json it could not read
    name = manifest.get("home_app") if isinstance(manifest, dict) else None
    if not isinstance(name, str):
        return None
    return name.strip() or None


class HomeAppTarget(Protocol):
    """The pod an import lands in, as the person importing reaches it."""

    async def chosen(self) -> str | None: ...
    async def app_names(self) -> set[str]: ...
    async def choose(self, app_name: str, *, unless_not: str | None) -> str | None:
        """Choose ``app_name`` if the choice is still ``unless_not``; else
        answer the choice found instead, unchanged."""
        ...


def created_apps(state: ImportState) -> set[str]:
    """The apps this import made, rather than found."""
    if state.plan is None:
        return set()
    return {
        step.name
        for step in state.plan.steps
        if step.kind is StepKind.APP
        and step.action is StepAction.CREATE
        and step.status is StepStatus.DONE
    }


async def carry_home_app(
    wanted: str, target: HomeAppTarget, *, created: set[str]
) -> list[str]:
    """Set ``wanted`` as the app the pod opens on when it has no other; answer
    what to tell the person importing, as import warnings."""
    stored = await target.chosen()
    apps = await target.app_names()
    # An app since removed is no choice: the pod opens on its first app.
    chosen = stored if stored in apps else None
    if chosen == wanted:
        return []
    if chosen is not None:
        return [_kept(chosen, wanted)]
    if wanted not in apps:
        missing = (
            f"This bundle opens on an app called '{wanted}', which it does not "
            "bring; it opens on its first app instead."
        )
        return [missing]
    # The bundle's own app, there before, is not another app: re-importing
    # into the pod it came from opens there as it already did.
    if apps - created - {wanted}:
        had_apps = (
            "This Lemma already had apps, so it still opens on its first one. "
            f"Choose '{wanted}' from its apps to open there instead."
        )
        return [had_apps]
    instead = await target.choose(wanted, unless_not=stored)
    return [] if instead is None else [_kept(instead, wanted)]


def _kept(chosen: str, wanted: str) -> str:
    return (
        f"Kept the app this Lemma opens on ('{chosen}'); the bundle's "
        f"('{wanted}') was not set. Choose it from the Lemma's apps to "
        "open there instead."
    )


class PodHomeAppTarget:
    """Production :class:`HomeAppTarget`: each call its own short unit of
    work, as the person importing."""

    def __init__(
        self, *, uow_factory: SessionUnitOfWorkFactory, pod_id: UUID, user_id: UUID
    ) -> None:
        self._uow_factory = uow_factory
        self._pod_id = pod_id
        self._user_id = user_id

    async def chosen(self) -> str | None:
        from app.core.authorization.scope import uow_scope
        from app.modules.pod.contracts.home_app import read_home_app

        async with uow_scope(self._uow_factory) as uow:
            return await read_home_app(uow.session, self._pod_id)

    async def app_names(self) -> set[str]:
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService
        from app.modules.apps.contracts.provisioning import list_app_names

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=self._user_id, pod_id=self._pod_id
            )
            async with context_scope(ctx):
                return set(
                    await list_app_names(
                        uow, pod_id=self._pod_id, user_id=self._user_id, ctx=ctx
                    )
                )

    async def choose(self, app_name: str, *, unless_not: str | None) -> str | None:
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService
        from app.modules.pod.contracts.home_app import read_home_app, set_home_app

        async with uow_scope(self._uow_factory) as uow:
            # Locked to the commit: a choice saved since the read is kept.
            now = await read_home_app(uow.session, self._pod_id, for_update=True)
            if now != unless_not:
                return now
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=self._user_id, pod_id=self._pod_id
            )
            async with context_scope(ctx):
                await set_home_app(uow, pod_id=self._pod_id, app_name=app_name, ctx=ctx)
                await uow.commit()
        return None


async def carry_to_pod(
    uow_factory: SessionUnitOfWorkFactory,
    state: ImportState,
    bundle_root: Path,
    *,
    target: HomeAppTarget | None = None,
) -> None:
    """Set the app the bundle's pod opened on, on the pod ``state`` imports
    into, once its steps have landed; what was kept instead joins the
    import's warnings. A template's start is a new pod with nothing in it, so
    it takes the bundle's choice like any other import."""
    wanted = await run_blocking(read_bundle_home_app, bundle_root, limiter="cpu_bound")
    if wanted is None:
        return
    target = target or PodHomeAppTarget(
        uow_factory=uow_factory, pod_id=state.pod_id, user_id=state.user_id
    )
    try:
        notes = await carry_home_app(wanted, target, created=created_apps(state))
    except DomainError as refused:
        notes = [f"The app this Lemma opens on was not set: {refused.message}"]
    except SQLAlchemyError as failed:
        raise_if_database_unavailable(failed)
        logger.warning(
            "pod_bundle.home_app.write_failed.degraded",
            import_id=str(state.import_id),
            exc_info=True,
        )
        notes = [
            (
                f"The app this Lemma opens on was not set; choose '{wanted}' "
                "from its apps to open there."
            )
        ]
    for note in notes:
        if note not in state.warnings:  # a resumed apply says it once
            state.warnings.append(note)


__all__ = [
    "HomeAppTarget",
    "PodHomeAppTarget",
    "carry_to_pod",
    "carry_home_app",
    "created_apps",
    "read_bundle_home_app",
]
