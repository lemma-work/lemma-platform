"""Dependencies for the decisions routes."""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from app.core.authorization.context import Context
from app.modules.decisions.domain.request import DecisionCaller
from app.modules.decisions.services.decision_service import DecisionService

#: Comfortably above the largest valid body -- 64 KiB of evidence, 32 KiB of
#: examples, a 16 KiB schema and an 8,000-character instruction, all escaped --
#: and far below the server-wide ceiling, so an oversize body is refused before
#: it is parsed.
MAX_BODY_BYTES = 512 * 1024

_WORKLOAD_ACTOR = re.compile(
    r"^(function|agent):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


def get_decision_service() -> DecisionService:
    return DecisionService()


DecisionServiceDep = Annotated[DecisionService, Depends(get_decision_service)]


async def bounded_body(request: Request) -> None:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "DECISION_INPUT_TOO_LARGE",
                "message": f"A decision request is at most {MAX_BODY_BYTES} bytes.",
            },
        )


def caller_from_context(ctx: Context) -> DecisionCaller:
    """Who is asking, as metering and the provider need it.

    A function or agent calling with a delegated token acts for the person who
    started it: the decision is attributed to that person and tagged with the
    workload, the way the workload's own model calls are.
    """
    if ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "DECISION_NEEDS_A_PERSON",
                "message": "A decision is asked on a person's behalf.",
            },
        )
    workload = _WORKLOAD_ACTOR.match(ctx.actor_id or "")
    kind, workload_id = (
        (workload.group(1), UUID(workload.group(2))) if workload else (None, None)
    )
    return DecisionCaller(
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        pod_id=ctx.pod_id,
        agent_id=workload_id if kind == "agent" else None,
        workload_type=kind,
        workload_id=workload_id,
        source_type="decision",
        source_id=ctx.actor_id,
    )
