"""``score_week``: count a teammate's week against its scorecard, in code.

The weekly review is a model writing about its own week, and the rule it runs
under is *numbers by code, words by the model*. This tool is the code half. It
reads the pod's ``scorecard`` table, counts every measure that is on
(``services/scorecard_counting``, the path the preview runs too), scores each
against its target (``domain/scorecard_scoring``), and records the result in
``scorecard_weeks`` -- so the review has numbers to quote and a later reader has
the same numbers to check it against.

Read-mostly by design. It writes one table, ``scorecard_weeks``, and only the
rows of the week it counted; it asks nobody anything; and every count runs as
the caller -- the conversation counts through ``readable_by``, the ledger
through the schedule module's visibility filter, a measure's SQL through the
datastore's own read-only path under the caller's row security. A scorecard
read by two people can therefore show two different weeks, and that is correct:
each sees the share of the teammate's work they are allowed to see.

The tool sits in the pod toolset, so it reaches the pod MCP surface the way the
other pod tools do, but its schema is deferred: a weekly job is no reason to
spend prompt budget on every turn of every conversation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Tool
from pydantic_ai.tools import RunContext

from app.modules.agent.domain.scorecard import (
    WEEKS_TABLE,
    MeasureStatus,
    ScorecardInputError,
    ScoringWindow,
    read_measures,
    scoring_window,
)
from app.modules.agent.domain.scorecard_scoring import MeasureResult, week_row
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.scorecard_source import (
    DatastoreScorecardSource,
)
from app.modules.agent.services.scorecard_counting import MAX_MEASURES, score_window
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import ScoreWeekRequest
from app.modules.agent.tools.pod.pod_common import run_pod_tool
from app.modules.agent.tools.pod.pod_data_access import PodServices
from app.modules.datastore.contracts import (
    ColumnSchema,
    DatastoreConflictError,
    DatastoreTableNotFoundError,
    TableContext,
)

TOOL_NAME = "score_week"

#: One read clears a week's earlier rows. A week holds one row per measure, so
#: this is room for many overlapping re-runs, not a page size.
_WEEK_ROWS_READ = MAX_MEASURES * 10

_WEEK_COLUMNS: tuple[JsonObject, ...] = (
    {"name": "id", "type": "UUID", "required": True, "auto": True},
    {"name": "week", "type": "DATE", "required": True},
    {"name": "key", "type": "TEXT"},
    {"name": "measure", "type": "TEXT"},
    {"name": "counted", "type": "FLOAT"},
    {"name": "total", "type": "FLOAT"},
    {"name": "value", "type": "FLOAT"},
    {"name": "shown", "type": "TEXT"},
    {"name": "met", "type": "BOOLEAN"},
    {"name": "status", "type": "TEXT", "required": True},
    {"name": "target_label", "type": "TEXT"},
)

_WEEKS_DESCRIPTION = (
    "What score_week counted: one row per scorecard measure per week, under the "
    "date the week ended. Counting a week again replaces that week's rows."
)

_QUOTE_RULE = (
    "Every number here was counted by the platform. Quote `shown` and `met` as "
    "given; a measure without a number has none, so say why instead of "
    "estimating one."
)


class MeasureScore(BaseModel):
    key: str
    measure: str
    status: MeasureStatus
    #: The display string to quote: "31 of 40", "none", "2", "1.6 days",
    #: "too few to judge (3)", "not counted".
    shown: str
    target_label: str
    counted: float | None = None
    total: float | None = None
    value: float | None = None
    met: bool | None = None
    #: Why a measure could not be counted, when it could not.
    reason: str | None = None

    @classmethod
    def of(cls, result: MeasureResult) -> MeasureScore:
        return cls(
            key=result.key,
            measure=result.measure,
            status=result.status,
            shown=result.shown,
            target_label=result.target_label,
            counted=result.counted,
            total=result.total,
            value=result.value,
            met=result.met,
            reason=result.reason,
        )


class ScoreWeekResult(BaseModel):
    success: bool = True
    #: The date the week ended: the key its rows are recorded under.
    week: date
    #: The window counted, ``[start, end)`` in UTC days.
    start: date
    end: date
    measures: list[MeasureScore]
    rows_written: int
    table: str = WEEKS_TABLE
    #: Scorecard rows past the first ``MAX_MEASURES``, on or off, that were not
    #: read this time.
    measures_skipped: int = 0
    note: str = _QUOTE_RULE


def scorecard_source(
    services: PodServices, *, pod_id: UUID, user_id: UUID
) -> DatastoreScorecardSource:
    """The scorecard's reads, as this tool call's caller."""
    return DatastoreScorecardSource(
        tables=services.table,
        records=services.record,
        session=services.uow.session,
        ctx=services.ctx,
        pod_id=pod_id,
        user_id=user_id,
    )


def _schema_name(services: PodServices, pod_id: UUID) -> str:
    return services.table.schema_manager.get_schema_name(pod_id)


async def _weeks_table(services: PodServices, pod_id: UUID) -> TableContext:
    """``scorecard_weeks``, created the first time a week is counted.

    Shared by the pod rather than private per person (``enable_rls`` off): the
    week's numbers are the teammate's record, and a review the whole team reads
    has to be written somewhere the whole team can read it.
    """
    try:
        table = await services.table.get_table(pod_id, WEEKS_TABLE, services.ctx)
    except DatastoreTableNotFoundError:
        try:
            table = await services.table.create_table(
                pod_id,
                WEEKS_TABLE,
                primary_key_column="id",
                columns=[ColumnSchema.model_validate(c) for c in _WEEK_COLUMNS],
                config={"description": _WEEKS_DESCRIPTION},
                enable_rls=False,
                ctx=services.ctx,
            )
        except DatastoreConflictError:
            # Another count created it between the read and the create.
            table = await services.table.get_table(pod_id, WEEKS_TABLE, services.ctx)
    return TableContext.from_table_entity(
        table, _schema_name(services, pod_id), events_enabled=True
    )


async def _replace_week(
    services: PodServices,
    *,
    pod_id: UUID,
    user_id: UUID,
    window: ScoringWindow,
    results: list[MeasureResult],
) -> int:
    """Write the week's rows, replacing whatever an earlier count wrote.

    Delete then insert rather than upsert: a measure turned off since the last
    count must lose its row for that week, and an upsert would leave it there.
    """
    table_ctx = await _weeks_table(services, pod_id)
    week = window.end.isoformat()
    earlier, _ = await services.record.list_records(
        table_ctx, user_id, limit=_WEEK_ROWS_READ, filters=[("week", "eq", week)]
    )
    if earlier:
        await services.record.bulk_delete_records(
            table_ctx, [record.id for record in earlier], user_id
        )
    rows: list[dict[str, object]] = [week_row(window.end, result) for result in results]
    return await services.record.bulk_create_records(table_ctx, rows, user_id)


async def _score(
    services: PodServices, *, pod_id: UUID, user_id: UUID, window: ScoringWindow
) -> JsonObject:
    source = scorecard_source(services, pod_id=pod_id, user_id=user_id)
    page = await source.scorecard_rows(MAX_MEASURES)
    if page is None:
        return {
            "success": False,
            "error": (
                "This pod has no `scorecard` table, so there is nothing to count "
                "and nothing was written. A scorecard is one row per measure: "
                "key, measure, counter, shape, aim, target, target_label, is_on."
            ),
        }
    # Off rows are not scored, and a proposal is off until somebody keeps it:
    # the week is never judged on a measure nobody agreed to.
    results = await score_window(source, read_measures(page.rows), window)
    written = await _replace_week(
        services, pod_id=pod_id, user_id=user_id, window=window, results=results
    )
    result = ScoreWeekResult(
        week=window.end,
        start=window.start,
        end=window.end,
        measures=[MeasureScore.of(result) for result in results],
        rows_written=written,
        measures_skipped=max(0, page.total - len(page.rows)),
    )
    return result.model_dump(mode="json")


async def score_week(
    ctx: RunContext[BaseAgentContext],
    request: ScoreWeekRequest,
) -> JsonObject:
    """Count this teammate's week against its scorecard and record the result.

    Reads the pod's `scorecard` table, counts every measure that is on for the
    seven days before `end`, scores each against its target, and writes one row
    per measure to `scorecard_weeks`, replacing that week's rows if it was
    counted before. Use it for the weekly review and whenever someone asks how
    the teammate is doing against its scorecard.

    Every number comes from the platform. Quote `shown`, `met` and
    `target_label` as given and never compute, round or estimate a figure
    yourself. A measure whose status is `not_counted`, `nothing_to_count` or
    `failed` has no number: say so, and give its `reason` if it has one. One
    whose status is `too_few` was counted but had too few rows to judge: quote
    its `shown` and do not call it met or missed.
    """
    try:
        window = scoring_window(request.end, today=datetime.now(timezone.utc).date())
    except ScorecardInputError as refused:
        return {"success": False, "error": str(refused)}
    pod_id = ctx.deps.pod_id

    async def op(services: PodServices) -> JsonObject:
        user_id = services.ctx.user_id
        if user_id is None:
            return {
                "success": False,
                "error": "A scorecard is counted for a person, and this call has none.",
            }
        return await _score(services, pod_id=pod_id, user_id=user_id, window=window)

    outcome = await run_pod_tool(
        ctx.deps,
        tool_name=TOOL_NAME,
        args=request.model_dump(mode="json"),
        op=op,
    )
    # `ToolReturn` is the image tools' branch of `run_pod_tool`; `op` above
    # only ever answers with a JSON object.
    return cast(JsonObject, outcome)


#: Deferred: found through tool search, so its schema is not paid for in every
#: prompt. See ``test_pod_default_visible_toolset_is_slim``.
score_week_tool = Tool(score_week, name=TOOL_NAME, defer_loading=True)
