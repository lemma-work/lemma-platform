"""``try_measure``: what a draft measure would have said over the last four weeks.

A teammate drafts its own scorecard from what people tell it about the job, and
a person keeps or drops each draft. They should decide having seen the numbers,
not a description of them -- and the teammate should find out a measure cannot
run, or reads nonsense, before it asks anybody to keep it. This runs the draft
through the same counting the weekly review uses, over four past weeks, and
writes nothing.

Same placement as ``score_week``: in the pod toolset, so the pod MCP surface
serves it, with its schema deferred behind tool search.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast

from pydantic import BaseModel
from pydantic_ai import Tool
from pydantic_ai.tools import RunContext

from app.modules.agent.domain.errors import ScorecardRefusedError
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.scorecard_preview import (
    DEFAULT_PREVIEW_WEEKS,
    preview_draft,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import TryMeasureRequest
from app.modules.agent.tools.pod.pod_common import run_pod_tool
from app.modules.agent.tools.pod.pod_data_access import PodServices
from app.modules.agent.tools.pod.scorecard import MeasureScore, scorecard_source

TOOL_NAME = "try_measure"

_HOW_TO_PROPOSE = (
    "These are the weeks this measure would have scored; nothing was saved. If "
    "they read wrong, change the measure and try it again. To propose it, write "
    "it into `scorecard` with is_on=false and proposed_from set to the person's "
    "own words; a person keeps it or drops it."
)


class WeekWindow(BaseModel):
    #: ``[start, end)`` in UTC days.
    start: date
    end: date


class TryMeasureResult(BaseModel):
    success: bool = True
    key: str
    measure: str
    #: Oldest first, one per entry of ``weeks``.
    windows: list[WeekWindow]
    weeks: list[MeasureScore]
    note: str = _HOW_TO_PROPOSE


async def try_measure(
    ctx: RunContext[BaseAgentContext],
    request: TryMeasureRequest,
) -> JsonObject:
    """Count a draft scorecard measure over the last four weeks, saving nothing.

    Use it before proposing a measure, every time. A measure counts rows of a
    table of your work: `unit_table` (one row per callback, conversation,
    promise), `time_column` (the date that places a row in a week), `test` (one
    SQL boolean over a row) and `shape` -- `share` of rows passing, `count` of
    rows passing, `median` of `value` over them, or `total`: their `value`
    added up, for reach, views or revenue. Every target is per week; a goal
    someone gives per month becomes a week's part of it, said in the measure.

    If the measure cannot run, the answer is `success: false` and the reason,
    naming what to change. Never write a proposal that fails here. When the
    weeks read right, propose it by writing it into the `scorecard` table with
    `is_on` false and `proposed_from` set to the person's own words; a person
    keeps or drops it. Quote the weeks' `shown` as given.
    """
    today = datetime.now(timezone.utc).date()
    pod_id = ctx.deps.pod_id

    async def op(services: PodServices) -> JsonObject:
        user_id = services.ctx.user_id
        if user_id is None:
            return {
                "success": False,
                "error": "A measure is counted for a person, and this call has none.",
            }
        source = scorecard_source(services, pod_id=pod_id, user_id=user_id)
        try:
            history = await preview_draft(
                source,
                request.model_dump(mode="json"),
                weeks=DEFAULT_PREVIEW_WEEKS,
                end=None,
                today=today,
            )
        except ScorecardRefusedError as refused:
            return {"success": False, "error": refused.message}
        measure = history.measures[0]
        return TryMeasureResult(
            key=measure.key,
            measure=measure.measure,
            windows=[
                WeekWindow(start=window.start, end=window.end)
                for window in history.windows
            ],
            weeks=[MeasureScore.of(result) for result in measure.weeks],
        ).model_dump(mode="json")

    outcome = await run_pod_tool(
        ctx.deps,
        tool_name=TOOL_NAME,
        args=request.model_dump(mode="json"),
        op=op,
    )
    # `ToolReturn` is the image tools' branch of `run_pod_tool`; `op` above
    # only ever answers with a JSON object.
    return cast(JsonObject, outcome)


#: Deferred like ``score_week``: drafting a scorecard is occasional work, and its
#: schema is not worth carrying in every prompt.
try_measure_tool = Tool(try_measure, name=TOOL_NAME, defer_loading=True)
