"""Asking a suspended DECISION node's question, between units of work.

The engine does not ask inside its transaction: the run row is locked for as
long as that lasts, and asking can climb to System One or a model. So a
DECISION node that has to ask suspends on a DECISION wait, and this -- run by
the `ask_workflow_decision` job once the wait has committed -- reads the wait
in one short unit of work, asks with no session open, then resumes or fails
the run in another, the way a finished agent or function resumes one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workflow.domain.decision_step import DecisionAsk, DecisionOutcome
from app.modules.workflow.domain.errors import DecisionStepError
from app.modules.workflow.domain.ports import DecisionPort


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """A DECISION wait still waiting, and who its run asks as."""

    ask: DecisionAsk
    user_id: UUID
    pod_id: UUID
    decisions: DecisionPort


class DecisionResumer(Protocol):
    """Reading a DECISION wait and settling it, within one unit of work each."""

    async def pending_decision(self, wait_ref: str) -> PendingDecision | None:
        """The wait's question, or None when nothing is waiting on it any more."""
        ...

    async def resume_for_decision(
        self, wait_ref: str, outcome: DecisionOutcome
    ) -> bool:
        """Resume the run with the answer. False when the wait is gone."""
        ...

    async def fail_for_decision(self, wait_ref: str, error: str) -> bool:
        """Fail the run with a refusal. False when the wait is gone."""
        ...


class DecisionStepService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        resumer_for: Callable[[SqlAlchemyUnitOfWork], DecisionResumer],
    ) -> None:
        self._uow_factory = uow_factory
        self._resumer_for = resumer_for

    async def answer(self, wait_ref: str) -> bool:
        """Ask the question `wait_ref` waits on and move its run on.

        False when there was nothing to ask: the run was cancelled, or another
        delivery of this job already answered it. Asking twice is harmless
        either way -- the decision is filed under the step's subject, so a
        second ask reads the first answer.
        """
        async with self._uow_factory() as uow:
            pending = await self._resumer_for(uow).pending_decision(wait_ref)
        if pending is None:
            return False
        try:
            outcome = await pending.decisions.decide(
                pending.ask, user_id=pending.user_id, pod_id=pending.pod_id
            )
        except DecisionStepError as exc:
            async with self._uow_factory() as uow:
                return await self._resumer_for(uow).fail_for_decision(
                    wait_ref, exc.message
                )
        async with self._uow_factory() as uow:
            return await self._resumer_for(uow).resume_for_decision(wait_ref, outcome)
