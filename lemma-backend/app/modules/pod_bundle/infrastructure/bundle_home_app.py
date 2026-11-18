"""The app a bundle's pod opens on, set on the pod it is imported into.

``pod.json -> home_app`` names one of the bundle's apps by its name (its
``apps/<name>`` directory). Without it a pod opens on its oldest app, and an
import creates a bundle's apps in name order, so a plugin whose bases come
along with it opened on whichever base sorted first.

What a pod already has is kept, as the rest of an import keeps it: the
bundle's choice is set only on a pod that has none and had no apps before this
import. A pod that already opens somewhere -- by a person's choice, or because
it had apps whose oldest it opens on -- goes on opening there, and the import
says so; a person moves it from the pod's apps. Set as the person importing,
through the pod's own update, once every step has landed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import POD_MANIFEST_FILE

from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.pod_bundle.domain.state import (
    ImportState,
    StepAction,
    StepKind,
    StepStatus,
)


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
    async def choose(self, app_name: str) -> None: ...


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
    chosen = await target.chosen()
    if chosen == wanted:
        return []
    if chosen is not None:
        kept = (
            f"Kept the app this Lemma opens on ('{chosen}'); the bundle's "
            f"('{wanted}') was not set. Choose it from the Lemma's apps to "
            "open there instead."
        )
        return [kept]
    apps = await target.app_names()
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
    await target.choose(wanted)
    return []


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

    async def choose(self, app_name: str) -> None:
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService
        from app.modules.pod.contracts.home_app import set_home_app

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=self._user_id, pod_id=self._pod_id
            )
            async with context_scope(ctx):
                await set_home_app(uow, pod_id=self._pod_id, app_name=app_name, ctx=ctx)
                await uow.commit()


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
    for note in await carry_home_app(wanted, target, created=created_apps(state)):
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
