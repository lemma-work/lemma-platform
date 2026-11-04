"""What a finished run left for the surface it was answering on.

Read by the surfaces module when it writes, or later sends, the reply a run
owes its thread -- after the run's own process may be gone, so from the run
row rather than from anything the run held in memory. Read-only; one row.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.private_notes import run_is_private
from app.modules.agent.infrastructure.models.conversation import AgentRunModel
from app.modules.agent.infrastructure.repositories.run_last_words import (
    answers_before_steers,
)


@dataclass(frozen=True, slots=True)
class RunReplyMaterial:
    status: str
    #: The run's final answer text, if it wrote one.
    answer: str | None
    #: The run's own user-facing sentence for its failure, if it failed.
    error: str | None
    #: Started by a private note in Lemma: nothing of it goes to the platform.
    private: bool
    #: What it answered before the person said more, oldest first: each a
    #: message of its own, ahead of the final answer (``domain/steered_answers``).
    earlier: tuple[str, ...] = ()


def answer_line_client_id(run_id: UUID) -> str:
    """The channel line a run's answer is, named by the run: wherever it is
    posted -- in Lemma, or for a platform -- a retry is the same line."""
    return f"run:{run_id}"


def earlier_answer_client_id(run_id: UUID, number: int) -> str:
    """The line of the run's ``number``-th answer given before a steer
    (from 1): one per answer, however often the reply is written."""
    return f"{answer_line_client_id(run_id)}:{number}"


def is_earlier_answer(client_message_id: str | None) -> bool:
    """Whether a run's line is an answer given before a steer, rather than its
    final answer: it rides no files and finishes no live stream."""
    if not client_message_id or not client_message_id.startswith("run:"):
        return False
    return client_message_id.count(":") == 2


def _answer_text(output: object) -> str | None:
    if isinstance(output, str):
        return output.strip() or None
    if isinstance(output, dict):
        for key in ("answer", "result"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


async def run_reply_material(
    uow: SqlAlchemyUnitOfWork, run_id: UUID
) -> RunReplyMaterial | None:
    run = await uow.session.get(AgentRunModel, run_id)
    if run is None:
        return None
    return RunReplyMaterial(
        status=str(run.status),
        answer=_answer_text(run.output_data),
        error=run.error,
        private=run_is_private(run.run_metadata),
        earlier=tuple(await answers_before_steers(uow.session, agent_run_id=run_id)),
    )


__all__ = [
    "RunReplyMaterial",
    "answer_line_client_id",
    "earlier_answer_client_id",
    "is_earlier_answer",
    "run_reply_material",
]
