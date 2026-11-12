"""What a bundle's pod owns, set on the pod it is imported into.

An export writes ``pod.json -> outcome`` when its pod owns something (the
shared normalizer does, in a template's shape). An import sets it once every
step has landed, so the measure counts the tables the bundle brought, and as
the person importing, through the same path a person setting it by hand takes:
a measure is read once and refused when they cannot read every row it counts.

A refused measure does not cost the rest. The sentence, kind, target, unit and
due date are kept without it, and the import says why, so the pod still says
what it owns and somebody sets the measure once the rows it counts are there.
A pod that already owns something keeps it: an import adds to a pod, it does
not change what that pod is for. A template import never comes here: a
template's outcome is set by the start that asks for its due date.

The import plan shows the same decision before anyone approves it
(:func:`plan_outcome`); :func:`outcome_action` is the one rule both follow.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.layout import POD_MANIFEST_FILE
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency.offload import run_blocking
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.pod.contracts.outcome import OutcomeInput, PodOutcome
from app.modules.pod_bundle.domain.state import (
    BundleSourceKind,
    ImportState,
    OutcomePlanAction,
    PlannedOutcome,
)


def read_bundle_outcome(bundle_root: Path) -> dict[str, object] | None:
    """``pod.json -> outcome`` as written, or ``None`` when there is none."""
    pod_path = bundle_root / POD_MANIFEST_FILE
    if not pod_path.is_file():
        return None
    try:
        manifest = loads_jsonc(pod_path.read_text(encoding="utf-8"))
    except ValueError:
        return None  # the plan already refused a pod.json it could not read
    block = manifest.get("outcome") if isinstance(manifest, dict) else None
    return block if isinstance(block, dict) else None


class OwnedOutcome(Protocol):
    """What the pod an import lands in owns now."""

    async def owned(self) -> PodOutcome | None: ...


class OutcomeTarget(OwnedOutcome, Protocol):
    """The pod an import sets what it owns on, as the person importing."""

    async def set(self, outcome: OutcomeInput) -> None:
        """Set ``outcome``, its measure read first; a refusal is a DomainError."""
        ...


def _same_promise(owned: PodOutcome, wanted: OutcomeInput) -> bool:
    """Whether the pod already owns the bundle's outcome, with or without its
    measure: an apply resumed after it was set finds it there."""
    fields = ("sentence", "kind", "target", "unit", "due")
    return all(getattr(owned, key) == getattr(wanted, key) for key in fields)


def outcome_action(owned: PodOutcome | None, wanted: OutcomeInput) -> OutcomePlanAction:
    """What an import does with ``wanted`` on a pod owning ``owned``: the one
    rule the plan shows and the apply follows."""
    if owned is None:
        return OutcomePlanAction.SET
    if _same_promise(owned, wanted):
        return OutcomePlanAction.SAME
    return OutcomePlanAction.KEEP


def _invalid(exc: ValidationError) -> str:
    """Where the block is wrong and why, as ``where: why``."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "outcome"
    return f"{where}: {first['msg']}"


async def plan_outcome(
    manifest: Mapping[str, object], pod: OwnedOutcome | None
) -> tuple[PlannedOutcome | None, list[str]]:
    """What the import plan says about ``pod.json -> outcome``, as ``manifest``
    has it: the outcome and what the apply will do with it, or ``None`` and
    why, as plan warnings, when there is none to set. No ``pod`` means this
    import sets none: a template's start does."""
    block = manifest.get("outcome")
    if block is None or pod is None:
        return None, []
    try:
        wanted = OutcomeInput.model_validate(block)
    except ValidationError as exc:
        return None, [f"What this bundle's pod owns will not be set: {_invalid(exc)}."]
    planned = PlannedOutcome(
        sentence=wanted.sentence,
        kind=wanted.kind.value,
        target=wanted.target,
        unit=wanted.unit,
        due=wanted.due,
        has_measure=wanted.measure is not None,
        action=outcome_action(await pod.owned(), wanted),
    )
    return planned, []


async def carry_outcome(
    block: Mapping[str, object], target: OutcomeTarget
) -> list[str]:
    """Set what the bundle's pod owned on ``target``; answer what to tell
    the person importing, as import warnings."""
    try:
        wanted = OutcomeInput.model_validate(block)
    except ValidationError as exc:
        return [f"What this bundle's pod owns was not set: {_invalid(exc)}."]
    owned = await target.owned()
    action = outcome_action(owned, wanted)
    if action is OutcomePlanAction.SAME:
        return []
    if owned is not None:  # KEEP
        kept = (
            f"Kept what this pod already owns ('{owned.sentence}'); the "
            f"bundle's ('{wanted.sentence}') was not set."
        )
        return [kept]
    try:
        await target.set(wanted)
        return []
    except DomainError as exc:
        if wanted.measure is None:
            return [f"What this bundle's pod owns was not set: {exc}"]
        refusal = exc
    try:
        await target.set(wanted.model_copy(update={"measure": None}))
    except DomainError as exc:
        return [f"What this bundle's pod owns was not set: {exc}"]
    unmeasured = (
        f"Set what this pod owns ('{wanted.sentence}') without its measure, which "
        f"could not be read ({refusal}). Set the measure again once it reads."
    )
    return [unmeasured]


class SessionOwnedOutcome:
    """Production :class:`OwnedOutcome` for the plan, read in the planning
    unit of work it is given."""

    def __init__(self, *, session: AsyncSession, pod_id: UUID) -> None:
        self._session = session
        self._pod_id = pod_id

    async def owned(self) -> PodOutcome | None:
        from app.modules.pod.contracts.outcome import read_pod_outcome

        return await read_pod_outcome(self._session, self._pod_id)


def owned_for_plan(state: ImportState, session: AsyncSession) -> OwnedOutcome | None:
    """What the plan reads the target pod's outcome through, or ``None`` for a
    template import, whose outcome its start sets (as :func:`carry` skips it)."""
    if state.source.kind is BundleSourceKind.TEMPLATE:
        return None
    return SessionOwnedOutcome(session=session, pod_id=state.pod_id)


class PodOutcomeTarget:
    """Production :class:`OutcomeTarget`: each call its own short unit of work,
    so a refused measure rolls back before the outcome is set without it."""

    def __init__(
        self, *, uow_factory: SessionUnitOfWorkFactory, pod_id: UUID, user_id: UUID
    ) -> None:
        self._uow_factory = uow_factory
        self._pod_id = pod_id
        self._user_id = user_id

    async def owned(self) -> PodOutcome | None:
        from app.core.authorization.scope import uow_scope
        from app.modules.pod.contracts.outcome import read_pod_outcome

        async with uow_scope(self._uow_factory) as uow:
            return await read_pod_outcome(uow.session, self._pod_id)

    async def set(self, outcome: OutcomeInput) -> None:
        from app.core.authorization.scope import context_scope, uow_scope
        from app.core.authorization.service import AuthorizationDataService
        from app.modules.datastore.contracts.outcome_measures import (
            build_outcome_measures,
        )
        from app.modules.pod.contracts.outcome import set_pod_outcome

        async with uow_scope(self._uow_factory) as uow:
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=self._user_id, pod_id=self._pod_id
            )
            async with context_scope(ctx):
                await set_pod_outcome(
                    uow,
                    pod_id=self._pod_id,
                    outcome=outcome,
                    ctx=ctx,
                    measures=build_outcome_measures(uow),
                )
                await uow.commit()


async def carry(
    uow_factory: SessionUnitOfWorkFactory,
    state: ImportState,
    bundle_root: Path,
    *,
    target: OutcomeTarget | None = None,
) -> None:
    """Set what the bundle's pod owned on the pod ``state`` imports into, once
    its steps have landed; what was kept instead, or set without its measure,
    joins the import's warnings. ``target`` is the pod as the importer reaches
    it, built from ``state`` when not given."""
    # A template's outcome is its start's to set (`start_use_cases`), with the
    # date a finish line is due, which only the start asks for.
    if state.source.kind is BundleSourceKind.TEMPLATE:
        return
    block = await run_blocking(read_bundle_outcome, bundle_root, limiter="cpu_bound")
    if block is None:
        return
    target = target or PodOutcomeTarget(
        uow_factory=uow_factory, pod_id=state.pod_id, user_id=state.user_id
    )
    for note in await carry_outcome(block, target):
        if note not in state.warnings:  # a resumed apply says it once
            state.warnings.append(note)


__all__ = [
    "owned_for_plan",
    "OutcomeTarget",
    "OwnedOutcome",
    "PodOutcomeTarget",
    "SessionOwnedOutcome",
    "carry",
    "carry_outcome",
    "outcome_action",
    "plan_outcome",
    "read_bundle_outcome",
]
