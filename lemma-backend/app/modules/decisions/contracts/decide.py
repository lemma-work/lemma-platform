"""Asking a decision from another module, and answering one.

Every caller outside `decisions` -- schedule filters, workflow steps, agent
tools, channels -- asks through these. Each call opens its own short units of
work, so a caller must not hold a database session across it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import JsonValue

from app.modules.decisions.config import decisions_settings
from app.modules.decisions.domain.deciders import DeciderDefinition, Lane
from app.modules.decisions.domain.decisions import (
    Answer,
    DecisionEntity,
    DecisionStatus,
    Rung,
)
from app.modules.decisions.domain.errors import (
    DeciderNotFoundError,
    DecisionRequestInvalidError,
)
from app.modules.decisions.domain.questions import AnswerValue, Option
from app.modules.decisions.services.decisions_service import (
    Asker,
    DecideRequest,
    RowResult,
    RowsResult,
)
from app.modules.decisions.services.wiring import build_decisions_service


async def decide(
    *,
    state: JsonValue,
    asker: Asker,
    decider: str | None = None,
    definition: DeciderDefinition | None = None,
    subject: str | None = None,
    options: Mapping[str, dict[str, Option]] | None = None,
    lane: Lane | None = None,
    record: bool = True,
) -> DecisionEntity:
    """Ask a named decider (`name` or `system:<name>`) or an inline definition.

    With a `subject`, the decision is asked once: a second call for the same
    decider and subject returns the recorded decision, never a fresh answer.
    """
    return await build_decisions_service().decide(
        DecideRequest(
            decider=decider,
            definition=definition,
            state=state,
            subject=subject,
            options=dict(options or {}),
            lane=lane,
            record=record,
        ),
        asker,
    )


async def decide_rows(
    *,
    rows: Sequence[JsonValue],
    asker: Asker,
    decider: str | None = None,
    definition: DeciderDefinition | None = None,
    options: Mapping[str, dict[str, Option]] | None = None,
    id_field: str | None = None,
    subject_prefix: str | None = None,
    record: bool = True,
) -> RowsResult:
    return await build_decisions_service().decide_rows(
        decider=decider,
        definition=definition,
        rows=rows,
        asker=asker,
        options=options,
        id_field=id_field,
        subject_prefix=subject_prefix,
        record=record,
    )


async def answer(
    *,
    decision_id: UUID,
    pod_id: UUID,
    answers: Mapping[str, AnswerValue],
    by: Rung,
    user_id: UUID,
) -> DecisionEntity:
    """Record a person's or an agent's answer. Only a person's teaches the decider."""
    return await build_decisions_service().answer(
        decision_id=decision_id,
        pod_id=pod_id,
        answers=answers,
        by=by,
        user_id=user_id,
    )


async def get_decision(
    *, decision_id: UUID, pod_id: UUID, viewer_id: UUID
) -> DecisionEntity:
    """One decision, if `viewer_id` may see it in this pod.

    Raises `DecisionNotFoundError` otherwise, the same for a decision that does
    not exist and one that is someone else's. A caller answering a decision
    reads it first to learn which decider it was asked of, and so what to
    authorize.
    """
    return await build_decisions_service().get(
        decision_id=decision_id, pod_id=pod_id, viewer_id=viewer_id
    )


def max_rows() -> int:
    """How many rows one `decide_rows` call may decide; more are refused.

    For a caller that reads rows from somewhere else first, so it can stop
    reading at the limit rather than load a whole table to be refused.
    """
    return decisions_settings.decisions_max_rows


__all__ = [
    "Answer",
    "Asker",
    "DeciderDefinition",
    "DeciderNotFoundError",
    "DecisionEntity",
    "DecisionRequestInvalidError",
    "DecisionStatus",
    "Lane",
    "Option",
    "RowResult",
    "RowsResult",
    "Rung",
    "answer",
    "decide",
    "decide_rows",
    "get_decision",
    "max_rows",
]
