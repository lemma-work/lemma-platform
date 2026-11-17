"""Record the pod app whose page sent a turn (``domain/app_turns``)."""

from __future__ import annotations

from starlette.requests import Request

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import AgentRunStartResult
from app.modules.agent.infrastructure.db.runs import Runs


async def record_app_turn(
    uow: SqlAlchemyUnitOfWork, request: Request, result: AgentRunStartResult
) -> None:
    """Mark the run a pinned app's page just sent a turn into. Commits, since
    the send already did; a request from no app writes nothing."""
    from app.core.app_pin import app_request_of

    app = app_request_of(request)
    if app is None or result.deduplicated:
        return
    await Runs(uow).record_sent_from_app(result.agent_run_id, app.app_id)
    await uow.commit()


__all__ = ["record_app_turn"]
