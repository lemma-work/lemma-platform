"""Starting a Lemma in one call: a pod that owns something, and the chat where
its agent sets itself up.

From a template or from a sentence in the person's own words, one transaction
writes all of it: the pod, what it owns, a first ``/plan.md``, and the setup
chat with the person's first message in it. Done as separate calls, a failure
part way left a pod that owned nothing, or owned something with no plan and no
agent working on it; now a failure leaves nothing.

A template also brings tables, a skill and schedules, which only a worker can
apply. Its archive is staged first, before anything is written, and the
import is queued in the same transaction as the rest; the chat is opened held,
and the job that applies the template starts its first turn when it is done
(``events/template_setup``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from app.core.authorization.scope import context_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.setup_conversations import (
    SetupConversation,
    open_setup_conversation,
)
from app.modules.datastore.contracts.pod_files import write_pod_file
from app.modules.pod.contracts.outcome import (
    PLAN_PATH,
    OutcomeInput,
    OutcomeKind,
    PodEntity,
    PodOutcome,
    create_owning_pod,
    set_starting_outcome,
)
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    BundleTemplateNotFoundError,
)
from app.modules.pod_bundle.application.template_imports import (
    StagedTemplate,
    stage_template,
)
from app.modules.pod_bundle.events.template_setup import SET_UP_LEMMA, SetUpLemmaJob
from app.modules.pod_bundle.events.work import BundleJobQueue, plan_key
from app.modules.pod_bundle.infrastructure.outcome_templates import (
    OutcomeTemplateCard,
    shipped_card,
    shipped_cards,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    PodBundleStateStore,
    get_pod_bundle_state_store,
)

#: Said at the top of a new plan, so whoever opens it knows what it is for.
PLAN_PREAMBLE = (
    "<!-- How this Lemma gets to what it owns. What it owns, its target and the "
    "measure that counts it are kept with the Lemma itself; this file is the "
    "plan. People and the Lemma both edit it, and a line they add ends with who "
    "added it and when. -->"
)


@dataclass(frozen=True, slots=True)
class StartRequest:
    organization_id: UUID
    #: A shipped template, or ``None`` for a Lemma started from ``outcome``.
    template: str | None
    outcome: OutcomeInput | None
    #: A template finish line's date.
    due: date | None
    name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class StartedLemma:
    pod: PodEntity
    conversation_id: UUID
    #: The template's import, to follow while it is applied; ``None`` without one.
    import_id: UUID | None


@dataclass(frozen=True, slots=True)
class _Owning:
    outcome: OutcomeInput
    name: str
    description: str | None
    card: OutcomeTemplateCard | None


class StartUseCases:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        state_store: PodBundleStateStore | None = None,
        staging: BundleStagingStorage | None = None,
        job_queue: BundleJobQueue | None = None,
    ) -> None:
        self._uow = uow
        self._state_store = state_store or get_pod_bundle_state_store()
        # Built when a template needs it: a Lemma started from its own words
        # stages nothing, and the store is opened (a directory made, for
        # local storage) as it is constructed.
        self._staging = staging
        self._job_queue = job_queue or BundleJobQueue()

    async def start(self, *, user_id: UUID, request: StartRequest) -> StartedLemma:
        owning = _what_it_owns(request)
        uow = self._uow
        staged: StagedTemplate | None = None
        if owning.card is not None:
            # Before anything is written: an upload is external I/O, and the
            # transaction holds nothing yet to keep it open across. A failure
            # later leaves an archive nobody imports, which the staging sweep
            # removes.
            async with connection_released(uow.session):
                staged = await self._stage_archive(owning.card.id)

        pod = await create_owning_pod(
            uow,
            organization_id=request.organization_id,
            owner_user_id=user_id,
            name=owning.name,
            description=owning.description,
        )
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod.id
        )
        # Bound for the block, because the datastore's file writes authorize
        # against the ambient context rather than one passed in.
        async with context_scope(ctx):
            kept = await set_starting_outcome(
                uow, pod_id=pod.id, outcome=owning.outcome, ctx=ctx
            )
            directory, name = PLAN_PATH.rsplit("/", 1)
            await write_pod_file(
                uow,
                pod_id=pod.id,
                directory=directory or "/",
                name=name,
                content=starter_plan(kept, owning.card).encode(),
                ctx=ctx,
            )
            setup = await open_setup_conversation(
                uow,
                pod_id=pod.id,
                user_id=user_id,
                text=setup_request(kept, owning.card),
                held=owning.card is not None,
            )
        import_id: UUID | None = None
        if owning.card is not None and staged is not None:
            import_id = await self._queue_template(
                pod_id=pod.id, user_id=user_id, staged=staged, setup=setup
            )
        await uow.commit()
        return StartedLemma(
            pod=pod.model_copy(
                update={"config": pod.config.model_copy(update={"outcome": kept})}
            ),
            conversation_id=setup.conversation_id,
            import_id=import_id,
        )

    async def _stage_archive(self, template: str) -> StagedTemplate:
        staging = self._staging or BundleStagingStorage()
        return await stage_template(staging, template, uuid4())

    async def _queue_template(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        staged: StagedTemplate,
        setup: SetupConversation,
    ) -> UUID:
        state = staged.import_state(pod_id=pod_id, user_id=user_id)
        await self._state_store.stage_import(self._uow, state)
        await self._job_queue.queue_job(
            self._uow,
            SET_UP_LEMMA,
            SetUpLemmaJob(
                import_id=staged.import_id,
                pod_id=pod_id,
                user_id=user_id,
                conversation_id=setup.conversation_id,
                run_id=setup.run_id,
            ),
            pod_id=pod_id,
            dedup_key=plan_key(staged.import_id),
        )
        return staged.import_id


def _what_it_owns(request: StartRequest) -> _Owning:
    if request.template is None:
        if request.outcome is None or request.name is None:
            raise BundleInvalidError(
                "Start a Lemma from a template, or from what it owns and a name."
            )
        if request.outcome.measure is not None:
            # Kept unread by `set_starting_outcome`, which is safe only for a
            # measure that ships with a template and counts the tables it brings.
            raise BundleInvalidError(
                "Nothing it could count exists yet, so it starts without a measure."
            )
        return _Owning(
            outcome=request.outcome,
            name=request.name,
            description=request.description,
            card=None,
        )
    card = shipped_card(request.template)
    if card is None:
        raise BundleTemplateNotFoundError(
            request.template, available=[card.id for card in shipped_cards()]
        )
    if card.kind is OutcomeKind.DATE and request.due is None:
        raise BundleInvalidError(
            f"{card.name} is a finish line: say the date it is due by."
        )
    if card.kind is OutcomeKind.WEEK and request.due is not None:
        raise BundleInvalidError(
            f"{card.name} is a bar held every week, so it has no due date."
        )
    return _Owning(
        outcome=card.outcome.for_due(request.due),
        name=request.name or card.name,
        description=request.description
        if request.description is not None
        else card.description,
        card=card,
    )


def starter_plan(outcome: PodOutcome, card: OutcomeTemplateCard | None) -> str:
    """The first ``/plan.md``: the four sections the workspace reads, with what
    it owns as the first thing that must be true and a template's own lines."""
    sections = (
        ("What must be true", [outcome.sentence]),
        ("How we work", card.plan.how if card else []),
        ("Who holds what", []),
        ("Never", card.plan.never if card else []),
    )
    body = "\n\n".join(
        "\n".join([f"## {title}", *(f"- {line}" for line in lines)])
        for title, lines in sections
    )
    return f"{PLAN_PREAMBLE}\n\n{body}\n"


def setup_request(outcome: PodOutcome, card: OutcomeTemplateCard | None) -> str:
    """The person's first message to the new Lemma's agent."""
    start = f'Set yourself up to own this: "{outcome.sentence}".'
    rest = (
        " Fill in the plan in /plan.md as you go: how we work, who holds what, "
        "and what you never do. Ask me whatever you need."
    )
    if card is not None:
        return (
            f"{start} You start from the {card.name} template, which brings its "
            "tables, a skill and schedules, and what you own is counted from its "
            "tables. Check what is here, then ask me where this work happens and "
            "connect it." + rest
        )
    return (
        f"{start} Look at what is already connected here and build what it "
        "takes: the tables that hold the work, an app if people need one, the "
        "schedules that keep it moving. Then set how it is counted with "
        "set_what_you_own, as one read-only query over those tables that answers "
        "a single number." + rest
    )


__all__ = [
    "PLAN_PREAMBLE",
    "StartRequest",
    "StartUseCases",
    "StartedLemma",
    "setup_request",
    "starter_plan",
]
