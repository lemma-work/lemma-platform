"""What a pod owns (``config.outcome``), for the modules that count it or set it.

The pod module keeps the outcome. ``datastore`` reads its measure and keeps its
weeks, the agent says it in its brief and sets it with its tool, and
``pod_bundle`` sets one on a Lemma it starts. They read it, set it and hear of
it here rather than reaching into the pod's config blob themselves.

A submodule rather than ``contracts/__init__``: it reaches the service layer
and the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context
from app.core.infrastructure.work import Fact, pod_namespace
from app.core.ports.outcome_measures import MeasureReading, OutcomeMeasures
from app.modules.pod.domain.pod_entities import (
    PLAN_PATH,
    OutcomeInput,
    OutcomeKind,
    PodEntity,
    PodOutcome,
)


class PodOutcomeChanged(Fact):
    """What a pod owns was set, changed or let go: its number is read again."""

    pod_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


async def read_pod_outcome(session: AsyncSession, pod_id: UUID) -> PodOutcome | None:
    """The pod's outcome, or ``None`` when it owns none -- or when what is
    stored no longer reads as one, which is said as owning nothing rather than
    failing every read of the pod."""
    from app.modules.pod.domain.pod_entities import PodConfig
    from app.modules.pod.infrastructure.models import Pod

    raw = await session.scalar(
        select(Pod.config).where(Pod.id == pod_id, Pod.is_deleted.is_(False))
    )
    return PodConfig.from_raw(raw).outcome if raw is not None else None


async def pods_with_measures(
    session: AsyncSession, *, after: UUID | None, limit: int
) -> list[UUID]:
    """Live pods whose outcome has a measure, by id, the page after ``after``."""
    from app.modules.pod.infrastructure.models import Pod
    from app.modules.pod.infrastructure.models.pod_models import MEASURED_POD

    statement = select(Pod.id).where(text(MEASURED_POD)).order_by(Pod.id).limit(limit)
    if after is not None:
        statement = statement.where(Pod.id > after)
    return list(await session.scalars(statement))


async def set_pod_outcome(
    uow,
    *,
    pod_id: UUID,
    outcome: OutcomeInput | None,
    ctx: Context,
    measures: OutcomeMeasures,
) -> tuple[PodOutcome | None, MeasureReading | None]:
    """Set what the pod owns as ``ctx``, or let it go with ``None``.

    The same path the API's ``PUT /pods/{pod_id}/outcome`` takes: ``ctx`` must
    be able to update the pod, and a new or changed measure is checked through
    ``measures`` before it is kept. Answers what is now kept, and what the
    measure answered when it was checked (``None`` when it was not).
    """
    from app.modules.pod.infrastructure.pod_repositories import PodRepository
    from app.modules.pod.services.pod_outcome_service import PodOutcomeService

    return await PodOutcomeService(PodRepository(uow), uow).set_outcome(
        pod_id, outcome, ctx, measures=measures
    )


#: How many numbered names a new pod tries after the one asked for is taken.
_NAME_TRIES = 50


async def create_owning_pod(
    uow,
    *,
    organization_id: UUID,
    owner_user_id: UUID,
    name: str,
    description: str | None,
) -> PodEntity:
    """A new pod, made by ``owner_user_id`` to own something; no commit.

    Named ``name``, or the first of ``name 2``, ``name 3``... that is free in
    the organization: two Lemmas started from one template are both called
    what the template is called, and the second must not fail for it. Pod's
    own ``create_pod`` does the rest -- that the maker is in the organization,
    that their plan has room, the pod's assistant and its mailbox.
    """
    from app.modules.pod.api.dependencies import get_pod_service
    from app.modules.pod.domain.errors import PodValidationError
    from app.modules.pod.domain.pod_names import normalize_pod_name
    from app.modules.pod.infrastructure.models import Pod

    try:
        wanted = normalize_pod_name(name)
    except ValueError as exc:
        raise PodValidationError(str(exc)) from exc
    candidates = [wanted] + [f"{wanted} {n}" for n in range(2, _NAME_TRIES + 1)]
    taken = set(
        await uow.session.scalars(
            select(Pod.name).where(
                Pod.organization_id == organization_id,
                Pod.is_deleted.is_(False),
                Pod.name.in_(candidates),
            )
        )
    )
    free = next(
        (candidate for candidate in candidates if candidate not in taken), wanted
    )
    return await get_pod_service(uow).create_pod(
        PodEntity(
            user_id=owner_user_id,
            organization_id=organization_id,
            name=free,
            description=description,
        ),
        owner_user_id,
    )


async def set_starting_outcome(
    uow, *, pod_id: UUID, outcome: OutcomeInput, ctx: Context
) -> PodOutcome:
    """What a pod made in this transaction owns, set by its maker.

    Only for a pod the caller has just created: its measure is kept without
    being read (``PodOutcomeService.set_starting_outcome`` says why), and a pod
    that already owns something is refused.
    """
    from app.modules.pod.infrastructure.pod_repositories import PodRepository
    from app.modules.pod.services.pod_outcome_service import PodOutcomeService

    return await PodOutcomeService(PodRepository(uow), uow).set_starting_outcome(
        pod_id, outcome, ctx
    )


__all__ = [
    "PLAN_PATH",
    "OutcomeInput",
    "OutcomeKind",
    "PodEntity",
    "PodOutcome",
    "PodOutcomeChanged",
    "create_owning_pod",
    "pods_with_measures",
    "read_pod_outcome",
    "set_pod_outcome",
    "set_starting_outcome",
]
