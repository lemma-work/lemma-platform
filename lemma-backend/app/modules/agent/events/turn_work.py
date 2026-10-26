"""Agent turns on the work runtime: ``agent.turn`` (and its automation twin), ``agent.title``.

A turn is written by ``contracts.turns.stage_agent_turn`` in the transaction
that creates its run, so a committed run always has the work that executes it.
What used to carry it -- an outbox row, the ``agent-events`` consumer group, a
streaq job, a Redis heartbeat per job and a ten-minute sweep for the runs all
of that lost -- is this one item:

* **one turn per conversation**: ``concurrency_key = conversation:<id>`` with a
  limit of 1, so two dispatchers never run two turns of one conversation;
* **a lease, not a sweep**: the runtime's own heartbeat (lease / 3) keeps the
  turn while a five-minute tool says nothing, and a worker that dies loses it
  within a lease -- the next claim resumes the run from its last committed step,
  settling the call in flight by its replay class (``services/turn_resume``);
* **every step fenced**: each step row (a tool call, its return, the answer)
  commits in a transaction holding the item's row lock under this claim's
  epoch (``ctx.hold_lease``), so a worker whose lease was taken over -- by
  another process, or by its own after its heartbeat could not reach the
  database -- adds no step to the run. The reaper and the next claim wait for
  a step in flight to commit, so the resumed turn reloads it;
* **tools stopped, not fenced**: a tool's own effects are not in a step's
  transaction, and most cannot be. Before each tool call it runs in-process
  (an Agent Host runs its own) the turn asks the
  local clock whether its last committed lease extension still covers it
  (``ctx.lease_alive``) and stops if not, so a worker that cannot renew starts
  no tool past about four fifths of a lease. A tool already running is not
  interrupted by that check (the heartbeat cancels the job a beat later). The
  main-database units a tool opens through the runner's factory read the
  item at each commit (``ctx.check_lease_at_commit``) and refuse to commit
  once another claim holds it -- a read, not a lock, so a takeover landing in
  the instant between the read and the commit is not caught. Nothing fences
  the rest, by construction: the lease row is in the main database, so a pod
  record or file written in the datastore database, an HTTP call, a message
  sent to Slack, a sandbox command or anything on an Agent Host can still
  happen once on a worker that has just lost the turn, and again on the
  worker that resumes it if its replay class says to run it again;
* **finalize fenced**: the run's terminal write, every follow-up it owes and the
  item's deletion commit in ``ctx.complete_work()``. A worker whose lease was
  taken over rolls its finalize back instead of finishing the run twice;
* **shutdown yields**: a SIGTERM stops the turn at the next step boundary, the
  item goes back to the queue uncharged (``Released``) and another worker
  resumes it in seconds. A user's Stop is different: it ends the run STOPPED;
* **dead means FAILED**: attempts, lease losses or the wall-clock cap exhausted
  -> ``on_dead`` fails the run and writes its follow-ups in the same commit.

Turns started by a schedule, a workflow or a trigger run as
``agent.turn.automation``, so a burst of them never queues ahead of a person.
The handler and its keys are the same.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import (
    DeadContext,
    Exponential,
    ItemFailure,
    JobContext,
    PermanentFailure,
    Released,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent.events.turn_payloads import AgentTurnWork, ConversationWork
from app.modules.agent.events.work_kinds import (
    AGENT_TITLE,
    AGENT_TURN,
    AGENT_TURN_AUTOMATION,
)

logger = get_logger(__name__)

#: The longest one turn may run, end to end: the run budget's unattended wall
#: clock plus room to finish. Past it the item goes dead and the run FAILED.
TURN_MAX_RUNTIME = timedelta(seconds=14_700)
#: A conversation runs one turn at a time.
TURN_CONCURRENCY = 1

#: Attempts are charged by a turn that threw; lease losses by a worker that
#: died under it. An OOM charges every turn in its process, so lease losses get
#: their own, larger budget (design §15 #8).
TURN_RETRY = RetryPolicy(
    max_attempts=3,
    max_lease_losses=5,
    unavailable_budget=timedelta(minutes=30),
    backoff=Exponential(
        base=timedelta(seconds=2), cap=timedelta(minutes=1), jitter=0.2
    ),
)
TITLE_RETRY = RetryPolicy(max_attempts=2, max_lease_losses=2)

INTERRUPTED_RUN_ERROR = "Agent run was interrupted (worker restart or crash)"
TIMED_OUT_RUN_ERROR = "Agent run ran past its time limit and was stopped"
#: The turn kept finding Lemma's database (or another dependency it needs)
#: unreachable for the whole unavailable budget. Nothing about the teammate or
#: its model was wrong, and the reader should know that.
UNAVAILABLE_RUN_ERROR = (
    "Lemma could not finish this turn: a service it depends on, such as its "
    "database, was unavailable for {minutes} minutes while the turn was "
    "retried. This has nothing to do with this teammate or its model settings. "
    "Everything up to the interruption is saved; send your message again to "
    "continue."
)


class TurnRunner(Protocol):
    async def execute(
        self,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        observer: object | None = None,
        yield_requested: Callable[[], bool] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class TurnFences:
    """The transactions a turn's effects commit in, each fenced by its claim.

    ``terminal``: the run's end, its follow-ups and the item's deletion.
    ``steps``: every step row, each its own short transaction.
    ``tools``: the main-database units the turn's tools (and its replays of
    open calls) open, checked against the claim at each commit (None: the
    runner's own factory).
    ``lease_alive``: asked before each tool call (None: never stop for it).
    """

    terminal: UnitOfWorkFactory
    steps: UnitOfWorkFactory
    tools: UnitOfWorkFactory | None = None
    lease_alive: Callable[[], bool] | None = None


#: Builds the runner one turn drives, given the fenced transactions its effects
#: commit in. The deployment's is ``_deployment_runner``; a test hands its own.
RunnerFactory = Callable[[TurnFences], TurnRunner]
#: Builds the observer that streams a turn onto the surface watching it.
ObserverFactory = Callable[[UnitOfWorkFactory], object | None]


def _deployment_runner(fences: TurnFences) -> TurnRunner:
    from app.modules.agent.services.agent_runner_service import AgentRunnerService
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.infrastructure.harness.registry import (
        build_harness_registry,
    )

    return AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=build_harness_registry(),
        terminal_units=fences.terminal,
        step_units=fences.steps,
        tool_units=fences.tools,
        lease_alive=fences.lease_alive,
    )


def _deployment_observer(uow_factory: UnitOfWorkFactory) -> object | None:
    from app.modules.agent_surfaces.contracts.egress import build_progress_observer

    return build_progress_observer(uow_factory=uow_factory)


class FencedTerminal:
    """The transaction a turn's end commits in: the item's fenced completion.

    Once the completion has committed (the run is terminal, the item gone),
    any later terminal write -- a failure path after a finished run -- gets an
    ordinary transaction, where the run's own compare-and-set finds it already
    ended and writes nothing.
    """

    def __init__(self, ctx: JobContext, fallback: UnitOfWorkFactory) -> None:
        self._ctx = ctx
        self._fallback = fallback

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        if self._ctx.completed:
            async with self._fallback() as uow:
                yield uow
            return
        async with self._ctx.complete_work() as uow:
            yield uow


class FencedSteps:
    """A step's transaction, committed only while this claim holds the lease.

    The fence is taken first, before the step's own writes, so the item's row
    is always locked before anything a step touches -- the order the fenced
    completion uses too.
    """

    def __init__(self, ctx: JobContext, units: UnitOfWorkFactory) -> None:
        self._ctx = ctx
        self._units = units

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with self._units() as uow:
            await self._ctx.hold_lease(uow.session)
            yield uow


class LeaseCheckedTools:
    """A tool's unit of work, refused at commit once this claim lost the lease.

    Not ``FencedSteps``: a tool holds its unit across I/O and nests units inside
    it, and a row lock taken there would hold the turn's steps and heartbeat
    behind the tool, or wait forever on the unit enclosing it.
    """

    def __init__(self, ctx: JobContext, units: UnitOfWorkFactory) -> None:
        self._ctx = ctx
        self._units = units

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with self._units() as uow:
            self._ctx.check_lease_at_commit(uow.session)
            yield uow


def pod_of(ns: str) -> UUID:
    return UUID(ns.removeprefix("pod:"))


def _turn_handler(build_runner: RunnerFactory, build_observer: ObserverFactory):
    async def run_turn(ctx: JobContext, payload: AgentTurnWork) -> None:
        from app.modules.agent.domain.errors import ConversationNotFoundError
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.agent.services.run_event_pump import TurnYielded

        ctx.raise_if_stopping()
        if ctx.item.attempts or ctx.item.lease_losses:
            logger.warning(
                "agent.turn.resuming",
                agent_run_id=str(payload.run_id),
                attempt=ctx.item.attempts,
                lease_losses=ctx.item.lease_losses,
            )
        uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        runner = build_runner(
            TurnFences(
                terminal=FencedTerminal(ctx, uow_factory),
                steps=FencedSteps(ctx, uow_factory),
                tools=LeaseCheckedTools(ctx, uow_factory),
                lease_alive=ctx.lease_alive,
            )
        )
        try:
            await runner.execute(
                agent_run_id=payload.run_id,
                user_id=payload.user_id,
                pod_id=pod_of(ctx.item.ns),
                agent_name=payload.agent_name,
                observer=build_observer(uow_factory),
                yield_requested=lambda: ctx.stopping,
            )
        except TurnYielded:
            logger.info("agent.turn.yielded", agent_run_id=str(payload.run_id))
            raise Released() from None
        except ConversationNotFoundError as exc:
            raise PermanentFailure(f"run {payload.run_id} is gone") from exc
        if not ctx.completed:
            # The run's end never committed (its finalize failed, and said so):
            # the run is still RUNNING, so the item is retried and resumes it.
            raise ItemFailure(f"run {payload.run_id} did not record its end")

    return run_turn


def dead_error(reason: str, last_error: str | None) -> str:
    """What the person reads on a turn that could not be completed."""
    if reason == "max_runtime":
        return TIMED_OUT_RUN_ERROR
    if reason == "unavailable_exhausted":
        minutes = int(TURN_RETRY.unavailable_budget.total_seconds() // 60)
        return UNAVAILABLE_RUN_ERROR.format(minutes=minutes)
    if reason == "attempts_exhausted" and last_error:
        return f"Agent run failed after repeated errors: {last_error[:500]}"
    return INTERRUPTED_RUN_ERROR


async def on_turn_dead(ctx: DeadContext) -> None:
    """The turn gave up: its run FAILED, and everything waiting on it hears."""
    from app.modules.agent.domain.value_objects import AgentRunStatus
    from app.modules.agent.infrastructure.db.runs import Runs
    from app.modules.agent.infrastructure.live import publish_conversation_event
    from app.modules.agent.domain.live_events import completed_payload, error_payload
    from app.modules.agent.services.run_followups import stage_run_followups

    try:
        payload = AgentTurnWork.model_validate(ctx.item.payload)
    except ValueError:
        return
    error = dead_error(ctx.reason, ctx.last_error)
    finish = await Runs(ctx.uow).finish_agent_run(
        agent_run_id=payload.run_id, status=AgentRunStatus.FAILED, error=error
    )
    if finish is None or not finish.updated:
        return
    await stage_run_followups(
        ctx.uow,
        run_id=payload.run_id,
        conversation_id=payload.conversation_id,
        finish=finish,
    )
    logger.warning(
        "agent.turn.dead",
        agent_run_id=str(payload.run_id),
        reason=ctx.reason,
    )

    async def _announce() -> None:
        await publish_conversation_event(
            payload.conversation_id, error_payload(payload.run_id, error)
        )
        await publish_conversation_event(
            payload.conversation_id,
            completed_payload(
                conversation_id=payload.conversation_id,
                agent_run_id=payload.run_id,
                status=finish.status.value,
                data={"error": error},
            ),
        )

    ctx.uow.after_commit(_announce)


async def generate_title(ctx: JobContext, payload: ConversationWork) -> None:
    from app.modules.agent.application.background.title import (
        ConversationTitleService,
    )
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    ctx.raise_if_stopping()
    await ConversationTitleService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker)
    ).generate_title_if_absent(payload.conversation_id)


def register_turn_work(
    registry: WorkRegistry,
    *,
    retry: RetryPolicy | None = None,
    lease: timedelta | None = None,
    max_runtime: timedelta | None = None,
    runner_factory: RunnerFactory | None = None,
    observer_factory: ObserverFactory | None = None,
) -> None:
    """Register the turn kinds; a test passes its own registry, lease and harness."""
    handler = _turn_handler(
        runner_factory or _deployment_runner,
        observer_factory or _deployment_observer,
    )
    for kind, cls in (
        (AGENT_TURN, WorkClass.INTERACTIVE),
        (AGENT_TURN_AUTOMATION, WorkClass.AUTOMATION),
    ):
        registry.handler(
            kind=kind,
            cls=cls,
            mode=WorkMode.JOB,
            payload=AgentTurnWork,
            retry=retry or TURN_RETRY,
            lease=lease,
            concurrency=TURN_CONCURRENCY,
            on_dead=on_turn_dead,
            max_runtime=max_runtime or TURN_MAX_RUNTIME,
        )(handler)
    registry.handler(
        kind=AGENT_TITLE,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=ConversationWork,
        retry=TITLE_RETRY,
        # One title at a time per conversation (keyed by ``title_key``).
        concurrency=1,
    )(generate_title)


register_turn_work(default_registry)
# The follow-ups a finalize writes, so any process that starts a turn can also
# finish one (a stop finalizes in the API).
import app.modules.agent.events.run_followup_work  # noqa: E402,F401


__all__ = [
    "INTERRUPTED_RUN_ERROR",
    "TURN_MAX_RUNTIME",
    "TURN_RETRY",
    "FencedSteps",
    "FencedTerminal",
    "LeaseCheckedTools",
    "TurnFences",
    "dead_error",
    "on_turn_dead",
    "register_turn_work",
]
