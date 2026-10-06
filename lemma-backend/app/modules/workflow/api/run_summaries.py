"""Run summaries with what a list of runs in flight is read for.

A summary on its own says a run is WAITING at `legal_review`. The two things
anyone looking at a page of them wants next -- what each run is about, and who
it is waiting on -- live in other rows: the title in the workflow's
`run_title` evaluated against the run's context, the assignee on the run's
active wait. Both are fetched here for a whole page at once, in at most three
statements however long the page is.
"""

from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

from app.modules.workflow.api.schemas import (
    WorkflowRunSummaryResponse,
    waiting_on_from_domain,
)
from app.modules.workflow.domain.context import RunContext
from app.modules.workflow.domain.run import TERMINAL_STATUSES, WorkflowRunEntity
from app.modules.workflow.domain.run_title import render_run_title
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowRunRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)


async def run_summaries(
    uow: SqlAlchemyUnitOfWork, runs: Sequence[WorkflowRunEntity]
) -> list[WorkflowRunSummaryResponse]:
    """Each run as a summary, titled and with its active wait."""
    if not runs:
        return []

    # Only a run still going can hold an active wait; asking about the rest
    # would be a wider IN for an answer known in advance.
    going = [run.id for run in runs if run.status not in TERMINAL_STATUSES]
    waits = await SqlAlchemyWorkflowRunWaitRepository(uow).active_for_runs(going)

    titles = await run_titles(uow, runs)

    out: list[WorkflowRunSummaryResponse] = []
    for run in runs:
        summary = WorkflowRunSummaryResponse.model_validate(run)
        summary.title = titles.get(run.id)
        summary.waiting_on = waiting_on_from_domain(waits.get(run.id))
        out.append(summary)
    return out


async def run_titles(
    uow: SqlAlchemyUnitOfWork, runs: Sequence[WorkflowRunEntity]
) -> dict[UUID, str]:
    """The title of every run whose workflow sets one and whose context has
    something for it to say. Contexts are read only for those runs."""
    templates = await SqlAlchemyWorkflowRepository(uow).run_titles_by_ids(
        list({run.flow_id for run in runs})
    )
    if not templates:
        return {}
    titled = [run for run in runs if run.flow_id in templates]
    contexts = await SqlAlchemyWorkflowRunRepository(uow).contexts_by_ids(
        [run.id for run in titled]
    )

    titles: dict[UUID, str] = {}
    for run in titled:
        try:
            view = RunContext.model_validate(contexts.get(run.id) or {}).to_view()
        except ValidationError:
            # A context written by an older shape of RunContext. Untitled is
            # the honest answer; failing the whole page is not.
            continue
        title = render_run_title(templates[run.flow_id], view)
        if title:
            titles[run.id] = title
    return titles
