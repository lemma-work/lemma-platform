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


@dataclass(frozen=True, slots=True)
class RunReplyMaterial:
    status: str
    #: The run's final answer text, if it wrote one.
    answer: str | None
    #: The run's own user-facing sentence for its failure, if it failed.
    error: str | None
    #: Started by a private note in Lemma: nothing of it goes to the platform.
    private: bool


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
    )


__all__ = ["RunReplyMaterial", "run_reply_material"]
