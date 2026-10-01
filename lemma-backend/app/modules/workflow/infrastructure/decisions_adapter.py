"""Asking a DECISION node's question of the decisions module.

The binding behind workflow's `DecisionPort`. It lives here rather than in
`decisions` because `decide` already is that module's one door, which every
caller asks through; what is workflow's own -- who may ask as the run's
person, which job does the asking, which question the node branches on --
stays with workflow.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceType
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.domain.job_queue import JobQueuePort
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.decisions.contracts.shapes import DecisionEntity, Lane
from app.modules.workflow.domain.decision_step import DecisionAsk, DecisionOutcome
from app.modules.workflow.domain.errors import DecisionStepError

#: The task that asks, registered in `workflow/events/handlers.py`.
ASK_DECISION_JOB = "ask_workflow_decision"
_SYSTEM_PREFIX = "system:"


class DecisionsAdapter:
    """`DecisionPort` over the streaq queue and the decisions contract."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory | None = None,
        job_queue: JobQueuePort | None = None,
    ) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
        self._job_queue = job_queue

    async def request(self, wait_ref: str) -> None:
        queue = self._job_queue or get_streaq_job_queue()
        await queue.enqueue(
            ASK_DECISION_JOB,
            wait_ref=wait_ref,
            _job_id=f"workflow-decision:{wait_ref}",
        )

    async def decide(
        self, ask: DecisionAsk, *, user_id: UUID, pod_id: UUID
    ) -> DecisionOutcome:
        # Here rather than at the top: the contract loads the engines, which
        # only asking needs -- not the API routes and jobs that build this.
        from app.modules.decisions.contracts.decide import Asker, decide

        organization_id = await self._authorize(ask, user_id=user_id, pod_id=pod_id)
        try:
            decision = await decide(
                state=ask.state,
                asker=Asker(
                    user_id=user_id, pod_id=pod_id, organization_id=organization_id
                ),
                decider=ask.decider,
                definition=ask.definition,
                subject=ask.subject,
                # A workflow step is sorted with nobody watching (design doc
                # §7.3), whatever lane the decider would pick for itself.
                lane=Lane.AMBIENT,
            )
        except DomainError as exc:
            raise DecisionStepError(exc.message) from exc
        return outcome_of(decision, ask)

    async def _authorize(
        self, ask: DecisionAsk, *, user_id: UUID, pod_id: UUID
    ) -> UUID | None:
        """The organization to charge, once the run's person may ask this decider.

        A system decider or an inline definition needs only the pod, which
        running the workflow already required. A pod decider is asked under its
        own grant, as it is over the API.
        """
        async with self._uow_factory() as uow:
            authz = create_authorization_data_service(uow)
            ctx = await authz.build_user_context(user_id=user_id, pod_id=pod_id)
            if ask.decider is None or ask.decider.startswith(_SYSTEM_PREFIX):
                return ctx.organization_id
            decider = await authz.resolve_resource_ref(
                resource_type=ResourceType.DECIDER,
                pod_id=pod_id,
                resource_name=ask.decider,
            )
            if decider is None:
                raise DecisionStepError(
                    f"No decider named {ask.decider!r} in this pod."
                )
            try:
                await ctx.require(Permissions.DECIDER_EXECUTE, decider)
            except DomainError as exc:
                raise DecisionStepError(exc.message) from exc
            return ctx.organization_id


def outcome_of(decision: DecisionEntity, ask: DecisionAsk) -> DecisionOutcome:
    """The answer to the question the node branches on, as the node records it.

    An open choice with a fallback still carries that fallback as its value;
    `answered_by` is left empty then, because no rung committed to it.
    """
    key = ask.question_key or _only_question(decision)
    if key not in decision.shape:
        raise DecisionStepError(f"The decider has no question {key!r} to branch on.")
    answer = decision.answers.get(key)
    if answer is None:
        return DecisionOutcome(decision_id=decision.id, open=list(decision.open))
    return DecisionOutcome(
        choice=answer.value,
        decision_id=decision.id,
        open=list(decision.open),
        answered_by=None if key in decision.open else answer.by.value,
    )


def _only_question(decision: DecisionEntity) -> str:
    if len(decision.shape) != 1:
        raise DecisionStepError(
            f"The decider asks {len(decision.shape)} questions; name the one "
            "to branch on in `question_key`."
        )
    return next(iter(decision.shape))
