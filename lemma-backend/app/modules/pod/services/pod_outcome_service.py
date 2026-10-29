"""Setting what a pod owns.

Its own path rather than ``PodService.update_pod``'s config merge, for two
reasons. That merge cannot remove a field -- a cleared outcome serialises as
absent and the merge keeps the old one -- and a measure has to be checked
before it is kept, which no other field of the config needs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.core.ports.outcome_measures import MeasureReading, OutcomeMeasures
from app.modules.pod.contracts.outcome import PodOutcomeChanged
from app.modules.pod.domain.errors import PodConflictError, PodNotFoundError
from app.modules.pod.domain.pod_entities import OutcomeInput, PodOutcome
from app.modules.pod.domain.ports import PodRepositoryPort

type Today = Callable[[], date]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def same_query(before: str | None, after: str | None) -> bool:
    """Whether a measure is unchanged.

    Exactly, not by its tokens: spacing inside a quoted literal or name is
    part of what the query reads, so any edit at all is a new measure, read
    and checked before it is kept.
    """
    if before is None or after is None:
        return before is after
    return before.strip() == after.strip()


class PodOutcomeService:
    def __init__(
        self,
        pod_repository: PodRepositoryPort,
        uow: SqlAlchemyUnitOfWork | None,
        today: Today = _utc_today,
    ) -> None:
        self._pods = pod_repository
        self._uow = uow
        self._today = today

    async def set_outcome(
        self,
        pod_id: UUID,
        outcome: OutcomeInput | None,
        ctx: Context,
        *,
        measures: OutcomeMeasures,
    ) -> tuple[PodOutcome | None, MeasureReading | None]:
        """Set what the pod owns, or let it go with ``None``.

        A new or changed measure is checked for ``ctx`` first; one that is
        unchanged is not, so somebody who may move the target or reword the
        sentence does not also need to read every row the measure counts --
        unless it counts a table whose row security its last check did not
        cover, which is what setting it again is for. Answers what is kept,
        and what the measure answered when checked.
        """
        await self._pods.lock(pod_id)
        pod = await self._pods.get(pod_id)
        if pod is None:
            raise PodNotFoundError()
        await ctx.require(
            Permissions.POD_UPDATE, ResourceRef.pod(pod_id, pod.organization_id)
        )
        before = pod.config.outcome
        reading: MeasureReading | None = None
        kept: PodOutcome | None = None
        if outcome is not None:
            checked: list[UUID] = []
            if outcome.measure is not None:
                if before is not None and same_query(before.measure, outcome.measure):
                    checked = list(before.row_security_checked)
                    must_check = bool(
                        await measures.unchecked(
                            pod_id=pod_id, sql=outcome.measure, checked=checked
                        )
                    )
                else:
                    must_check = True
                if must_check:
                    result = await measures.check(
                        pod_id=pod_id, sql=outcome.measure, ctx=ctx
                    )
                    reading = result.reading
                    checked = sorted(result.row_security_checked)
            kept = PodOutcome(
                **outcome.model_dump(),
                since=self._since(before, outcome),
                row_security_checked=checked,
            )
        if kept == before:
            return kept, reading
        config = pod.config.model_copy(update={"outcome": kept})
        await self._pods.update(pod.model_copy(update={"config": config}))
        if self._uow is not None:
            await facts.emit(self._uow, PodOutcomeChanged(pod_id=pod_id))
        return kept, reading

    async def set_starting_outcome(
        self, pod_id: UUID, outcome: OutcomeInput, ctx: Context
    ) -> PodOutcome:
        """What a pod owns from the moment it is made, set by its maker.

        Its measure is kept without being read. It counts the tables a
        template brings, which are applied after this transaction commits, so
        there is nothing to read yet; and the check that read stands for is
        one its maker passes by construction -- they made the pod, administer
        it, and own every table the template adds. No reading is asked for
        either, for the same reason: the sweep reads it once it can.

        Nothing is recorded as checked, so the measure is read only while the
        tables it counts have no row security -- which is how every shipped
        template brings them (``test_outcome_templates`` holds them to it).
        A table given row security later is one its maker sets the measure
        again for, like any other pod's.
        """
        await self._pods.lock(pod_id)
        pod = await self._pods.get(pod_id)
        if pod is None:
            raise PodNotFoundError()
        await ctx.require(
            Permissions.POD_UPDATE, ResourceRef.pod(pod_id, pod.organization_id)
        )
        if pod.config.outcome is not None:
            raise PodConflictError("This pod already owns something.")
        kept = PodOutcome(**outcome.model_dump(), since=self._today())
        config = pod.config.model_copy(update={"outcome": kept})
        await self._pods.update(pod.model_copy(update={"config": config}))
        return kept

    def _since(self, before: PodOutcome | None, after: OutcomeInput) -> date:
        """The day the pod took this on: kept through rewording and a new
        target, and started again when it becomes the other kind of thing."""
        if before is not None and before.kind is after.kind and before.since:
            return before.since
        return self._today()


__all__ = ["PodOutcomeService", "same_query"]
