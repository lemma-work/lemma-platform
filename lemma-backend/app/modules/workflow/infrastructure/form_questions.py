"""A workflow's form, as a question waiting on its assignee.

A ``FORM`` node assigned to a pod member puts a line in that member's own
channel asking for it (``channels.contracts.post`` with a ``FORM`` question):
in their inbox beside every other question put to them, pushed to them where
they are, answered in place (``answer_form``) or in the workflow's own screen.
Either way the engine validates what is submitted and closes the question
(``form_answered``); a run that ends cancels whatever it still asks. A
question whose time runs out (the channels' sweeper, at the wait's own
ceiling) fails the run as a form nobody answered (``events.run_work``).

The ref is ``wf:{run}:{node}:{wait}``: a run that comes back to a node asks
again, as a new question, rather than finding the answered one.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.channels.contracts import (
    Asker,
    NewLine,
    NewQuestion,
    Principal,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.questions import AnswerRefused, Answering
from app.modules.pod.contracts.members import pod_member_user_id

logger = get_logger(__name__)

#: What a form's question ``ref`` starts with.
FORM_REF = "wf"


def form_ref(run_id: UUID, node_id: str, wait_id: UUID) -> str:
    return f"{FORM_REF}:{run_id}:{node_id}:{wait_id}"


def form_wait_id(ref: str) -> UUID | None:
    """The wait a form's question asks for, from its ref."""
    kind, _, rest = ref.partition(":")
    if kind != FORM_REF:
        return None
    try:
        return UUID(rest.rpartition(":")[2])
    except ValueError:
        return None


def describe_fields(schema: Mapping[str, object] | None) -> str:
    """A one-line summary of what the form wants, for the line's words."""
    if not isinstance(schema, Mapping):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return ""
    labels = []
    for name, spec in properties.items():
        title = None
        if isinstance(spec, Mapping):
            title = spec.get("title") or spec.get("description")
        labels.append(str(title or name))
    return ", ".join(labels[:8])


class FormQuestions:
    """Asks, answers and withdraws a run's forms, in the engine's transaction."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def ask_form(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        wait_id: UUID,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, object] | None,
        actor_user_id: UUID | None,
        expires_at: datetime | None,
        node_label: str | None = None,
    ) -> None:
        assignee = await pod_member_user_id(
            self._uow, pod_id=pod_id, pod_member_id=assigned_pod_member_id
        )
        if assignee is None:
            logger.warning(
                "workflow.form_question.assignee_unresolved.degraded",
                run_id=str(run_id),
                node_id=node_id,
            )
            return
        ref = form_ref(run_id, node_id, wait_id)
        fields = describe_fields(schema)
        # The form's own name, as its author labelled the step, says what is
        # asked; the workflow's says what for.
        label = " ".join((node_label or "").split())
        title = (
            f"{flow_name or 'A workflow'}: {label}"
            if label
            else f"{flow_name or 'A workflow'} needs your input"
        )
        channel = await direct_channel(self._uow, pod_id=pod_id, user_id=assignee)
        agent_id = effective_agent_id(None, pod_id=pod_id)
        await post(
            self._uow,
            channel_id=channel.id,
            line=NewLine(
                sender=Sender.agent(
                    agent_id, on_behalf_of=Principal.member(actor_user_id or assignee)
                ),
                text=f"{title}." + (f" It asks for: {fields}." if fields else ""),
                # A retried job, or the node asked twice, is the same line.
                client_message_id=f"wf:{wait_id}",
            ),
            question=NewQuestion(
                kind=QuestionKind.FORM,
                ref=ref,
                asker=Asker.workflow(run_id, f"{node_id}:{wait_id}"),
                question={
                    "tool": "workflow_form",
                    "prompt": title,
                    "run_id": str(run_id),
                    "flow_id": str(flow_id),
                    "node_id": node_id,
                    "wait_id": str(wait_id),
                    # The concrete, template-resolved schema on the wait, so
                    # the form renders where it is asked.
                    "schema": schema,
                },
                awaits=Principal.member(assignee),
                expires_at=expires_at,
            ),
        )

    async def form_answered(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        node_id: str,
        wait_id: UUID,
        data: dict[str, object],
        by_user_id: UUID | None,
    ) -> None:
        await channel_questions.close_ref(
            self._uow,
            pod_id=pod_id,
            ref=form_ref(run_id, node_id, wait_id),
            state=QuestionState.ANSWERED,
            answer=dict(data),
            by=Principal.member(by_user_id) if by_user_id else None,
        )

    async def run_ended(self, *, pod_id: UUID, run_id: UUID) -> None:
        await channel_questions.cancel_asked_by(
            self._uow, QuestionAsker.WORKFLOW, workflow_run_id=run_id
        )


@channel_questions.answers_for(QuestionAsker.WORKFLOW)
async def answer_form(answering: Answering) -> Mapping[str, object] | None:
    """A form answered on its line: submitted through the engine, which checks
    it is theirs, validates it, and closes the question with what it took."""
    from app.modules.workflow.contracts.forms import submit_workflow_form

    question = answering.question.question
    user_id = answering.by.user_id
    try:
        run_id = UUID(str(question.get("run_id")))
    except ValueError:
        raise AnswerRefused(
            "This form cannot be answered here", status_code=404
        ) from None
    if user_id is None:
        raise AnswerRefused("This form is not yours to answer", status_code=403)
    submitted, said = await submit_workflow_form(
        run_id=run_id,
        node_id=str(question.get("node_id") or ""),
        inputs=dict(answering.answer),
        requester_user_id=user_id,
    )
    if not submitted:
        raise AnswerRefused(said, status_code=422)
    # The engine closed the question with the values it took, defaults merged.
    return None


def build_form_questions(uow: SqlAlchemyUnitOfWork) -> FormQuestions:
    return FormQuestions(uow)


__all__ = [
    "FORM_REF",
    "FormQuestions",
    "answer_form",
    "build_form_questions",
    "describe_fields",
    "form_ref",
    "form_wait_id",
]
