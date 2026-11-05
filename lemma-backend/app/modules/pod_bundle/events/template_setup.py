"""A Lemma started from a template: apply the template, then start its agent.

``pod_bundle.set_up_lemma`` is written with the Lemma itself -- its pod, what
it owns, its plan and its held setup chat, all in one transaction. It plans the
template's import and applies it straight away, without the confirmation an
uploaded bundle waits for: the person chose a template that ships with this
server, for a pod that has nothing in it yet to overwrite. Then, in the item's
completion, it starts the setup chat's first turn, so the agent never works in
a pod whose tables are half there.

The turn starts however the import ends. Applied, the agent finds the
template's tables, skill and schedules; failed or stopped short, it finds what
landed and says so. A plan that would need the person after all -- variables
to fill, or a destructive step -- is left waiting for them as any import is,
and the agent starts beside it. If the item dies, its ``on_dead`` hook ends the
import and starts the turn in the same commit: no setup chat is left waiting on
a job that will not come back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.infrastructure.work import (
    DeadContext,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.modules.pod_bundle.domain.state import ImportState, ImportStatus
from app.modules.pod_bundle.events.work import (
    IMPORT,
    MAX_RUNTIME,
    RETRY,
    ImportJob,
    end_dead_job,
    run_body,
)

if TYPE_CHECKING:
    from app.modules.agent.contracts.setup_conversations import SetupConversation

SET_UP_LEMMA = "pod_bundle.set_up_lemma"

#: Where an import still has planning to do.
_UNPLANNED = frozenset(
    {ImportStatus.QUEUED, ImportStatus.FETCHING, ImportStatus.PLANNING}
)


class SetUpLemmaJob(ImportJob):
    """The template import to apply, and the setup chat to start after it."""

    conversation_id: UUID
    run_id: UUID

    def setup(self) -> SetupConversation:
        from app.modules.agent.contracts.setup_conversations import SetupConversation

        return SetupConversation(
            conversation_id=self.conversation_id,
            run_id=self.run_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
        )


def applies_without_asking(state: ImportState) -> bool:
    """Whether a planned import can be applied with nobody confirming it."""
    plan = state.plan
    if plan is None or plan.has_destructive_steps:
        return False
    return not any(variable.required for variable in plan.variables)


async def apply_template(context: dict[str, str | None]) -> None:
    """Plan the staged template if it is not yet, then apply it.

    Each half is the ordinary import's own body, so a retry resumes where the
    last attempt stopped: a plan already made is not made again, and apply
    picks up at its first pending step.
    """
    from app.modules.pod_bundle.events.handlers import (
        apply_pod_import,
        plan_pod_import,
    )
    from app.modules.pod_bundle.infrastructure.state_store import (
        get_pod_bundle_state_store,
    )

    store = get_pod_bundle_state_store()
    import_id = UUID(str(context["import_id"]))
    state = await store.get_import(import_id)
    if state is None or state.is_terminal:
        return
    if state.status in _UNPLANNED:
        await plan_pod_import(context)
        state = await store.get_import(import_id)
        if state is None or state.status is not ImportStatus.AWAITING_CONFIRMATION:
            return
    if (
        state.status is ImportStatus.AWAITING_CONFIRMATION
        and not applies_without_asking(state)
    ):
        return
    await apply_pod_import(context)


async def run_set_up_lemma(ctx: JobContext, payload: SetUpLemmaJob) -> None:
    from app.modules.agent.contracts.setup_conversations import start_setup_turn

    await run_body(ctx, payload, apply_template, IMPORT)
    async with ctx.complete_work() as uow:
        await start_setup_turn(uow, payload.setup())


async def end_dead_set_up_lemma(ctx: DeadContext) -> None:
    """End the import as any dead bundle job's is, and start the agent anyway."""
    from app.modules.agent.contracts.setup_conversations import start_setup_turn

    await end_dead_job(ctx)
    payload = SetUpLemmaJob.model_validate(ctx.item.payload)
    await start_setup_turn(ctx.uow, payload.setup())


def register_set_up_lemma_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SET_UP_LEMMA,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=SetUpLemmaJob,
        retry=RETRY,
        concurrency=1,
        on_dead=end_dead_set_up_lemma,
        max_runtime=MAX_RUNTIME,
    )(run_set_up_lemma)


register_set_up_lemma_work(default_registry)
