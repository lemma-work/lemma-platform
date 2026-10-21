"""The webhook request's side of Lemma's own Slack app's lane.

``handle_platform_webhook`` hands a delivery here once verification named it
the manager's (``InboundVerification.is_managers``) -- before onboarding and
before ``accept()``, so nothing the manager receives is stored as an inbound
message, classified, or applied as a group update.
"""

from __future__ import annotations

from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import JsonValue

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.platforms.slack.manager_parser import (
    parse_manager_input,
)
from app.modules.agent_surfaces.services import slack_manager


async def answer_manager(
    payload: dict[str, JsonValue],
    *,
    source_event_id: str,
    uow_factory: UnitOfWorkFactory,
) -> Response:
    """Answer what Slack's three seconds require, queue the rest as ids, reply.

    A modal submission's body is protocol -- a ``response_action`` -- so it is
    what Slack gets back; every other delivery gets an empty 200.
    """
    answer = await slack_manager.answer_now(uow_factory, parse_manager_input(payload))
    if answer.queued is not None:
        await slack_manager.queue(
            uow_factory,
            answer.queued,
            source_event_id=source_event_id,
            delay=answer.delay,
        )
    if answer.body is not None:
        return JSONResponse(answer.body)
    return Response(status_code=200)
