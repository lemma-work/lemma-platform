from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from streaq.task import TaskStatus

from app.modules.agent.domain.events import (
    AgentRunCompletedEvent,
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.events.handlers import conversation_title_job_id
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.events import handlers
from app.modules.agent.domain.run_projections import (
    StaleAgentRunRef,
    StrandedConversationRef,
)
from app.modules.test_support.fakes import PassthroughEventInbox


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append((message, args))


class _JobQueue:
    def __init__(self, status: TaskStatus) -> None:
        self._status = status
        self.abort_called = False
        self.enqueued: list[tuple[str, dict, str | None]] = []

    async def status(self, job_id: str) -> TaskStatus:
        return self._status

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        self.abort_called = True
        return True

    async def enqueue(
        self, task_name: str, *, context: dict[str, object], _job_id: str | None = None
    ):
        self.enqueued.append((task_name, context, _job_id))
        return object()


class _UowFactory:
    def __init__(self) -> None:
        self.events: list[object] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def collect_events(self, events: list[object]) -> None:
        self.events.extend(events)


class _ConversationRepository:
    def __init__(self, uow) -> None:
        self.uow = uow

    async def finish_agent_run(
        self,
        *,
        agent_run_id,
        status: AgentRunStatus,
    ):
        return SimpleNamespace(status=status, updated=True)

    def collect_events(self, events: list[object]) -> None:
        self.uow.collect_events(events)


@pytest.mark.asyncio
async def test_stop_requested_for_queued_run_finishes_without_streaq_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime: list[tuple[object, dict[str, object]]] = []

    async def publish_realtime(conversation_id, payload) -> None:
        realtime.append((conversation_id, payload))

    monkeypatch.setattr(handlers, "ConversationRepository", _ConversationRepository)
    monkeypatch.setattr(handlers, "publish_conversation_event", publish_realtime)

    job_queue = _JobQueue(TaskStatus.SCHEDULED)
    stop_event = AgentRunStopRequestedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        user_id=uuid4(),
    )

    uow_factory = _UowFactory()
    await handlers.handle_agent_control_event(
        stop_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=uow_factory,
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.abort_called is False
    assert len(uow_factory.events) == 1
    event = uow_factory.events[0]
    assert isinstance(event, AgentRunCompletedEvent)
    assert event.status == AgentRunStatus.STOPPED
    assert event.data == {
        "aborted": False,
        "task_status": TaskStatus.SCHEDULED.value,
    }
    assert realtime == [
        (
            stop_event.conversation_id,
            {
                "type": "completed",
                "agent_run_id": str(stop_event.agent_run_id),
                "data": {
                    "conversation_id": str(stop_event.conversation_id),
                    "status": AgentRunStatus.STOPPED.value,
                    "aborted": False,
                    "task_status": TaskStatus.SCHEDULED.value,
                },
            },
        )
    ]


def test_title_task_is_registered_on_worker() -> None:
    # Importing ``handlers`` (top of this module) runs the @streaq_task
    # decorators, so the worker the subprocess runs knows the title task.
    from app.core.infrastructure.jobs.streaq_runtime import streaq_worker

    assert "generate_conversation_title" in streaq_worker.registry
    assert "process_agent_run" in streaq_worker.registry


@pytest.mark.asyncio
async def test_completed_event_enqueues_dedup_title_job() -> None:
    job_queue = _JobQueue(TaskStatus.SCHEDULED)
    completed_event = AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    )

    await handlers.handle_agent_control_event(
        completed_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueued == [
        (
            "generate_conversation_title",
            {"conversation_id": str(completed_event.conversation_id)},
            conversation_title_job_id(completed_event.conversation_id),
        )
    ]


@pytest.mark.asyncio
async def test_started_event_also_enqueues_dedup_title_job() -> None:
    """The title only needs the user's first message, already saved by the
    time this event fires, so it starts on run-start rather than waiting for
    the run to finish -- a long-running turn should not leave the
    conversation title-less for its whole duration."""
    job_queue = _JobQueue(TaskStatus.SCHEDULED)
    started_event = AgentRunStartedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_name="hello",
    )

    await handlers.handle_agent_control_event(
        started_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
        inbox=PassthroughEventInbox(),
    )

    assert (
        "process_agent_run",
        {
            "agent_run_id": str(started_event.agent_run_id),
            "conversation_id": str(started_event.conversation_id),
            "user_id": str(started_event.user_id),
            "pod_id": str(started_event.pod_id),
            "agent_name": started_event.agent_name,
        },
        handlers.agent_run_job_id(started_event.agent_run_id),
    ) in job_queue.enqueued
    assert (
        "generate_conversation_title",
        {"conversation_id": str(started_event.conversation_id)},
        conversation_title_job_id(started_event.conversation_id),
    ) in job_queue.enqueued


@pytest.mark.asyncio
async def test_stop_requested_for_running_run_is_left_for_cooperative_stop() -> None:
    job_queue = _JobQueue(TaskStatus.RUNNING)
    stop_event = AgentRunStopRequestedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        user_id=uuid4(),
    )

    await handlers.handle_agent_control_event(
        stop_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.abort_called is False


def test_reconcile_orphaned_agent_runs_cron_registered() -> None:
    from app.core.infrastructure.jobs.streaq_runtime import streaq_worker

    assert "reconcile_orphaned_agent_runs" in streaq_worker.registry


@pytest.mark.asyncio
async def test_reconcile_orphaned_agent_runs_finalizes_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconciler marks stale runs FAILED and publishes lifecycle + SSE
    events only for runs it actually transitioned (idempotent under races)."""
    conv1, run1 = uuid4(), uuid4()
    conv2, run2 = uuid4(), uuid4()
    stale = [
        StaleAgentRunRef(id=run1, conversation_id=conv1),
        StaleAgentRunRef(id=run2, conversation_id=conv2),
    ]
    finished: list[object] = []
    realtime: list[tuple[object, dict]] = []

    claimed: list = []

    class _Repo:
        def __init__(self, uow) -> None:
            self.uow = uow

        async def list_runs_stuck_stopping(self, *, cutoff_seconds, limit=200):
            return []

        async def list_active_runs_pending_liveness(
            self, *, cutoff_seconds, decided_after_seconds, limit=200
        ):
            return []

        async def list_stale_active_runs(self, *, cutoff_seconds, limit=200):
            return stale

        async def list_conversations_stranded_by_a_finished_run(
            self, *, cutoff_seconds, limit=200
        ):
            return []

        async def finish_agent_run(self, *, agent_run_id, status, error=None):
            finished.append(agent_run_id)
            # run2 was already terminal (race) -> not updated -> no events.
            return SimpleNamespace(updated=agent_run_id == run1, status=status)

        async def claim_usage_reservation(self, *, agent_run_id):
            claimed.append(agent_run_id)
            return

        def collect_events(self, events: list[object]) -> None:
            self.uow.collect_events(events)

    async def publish_realtime(conversation_id, payload) -> None:
        realtime.append((conversation_id, payload))

    monkeypatch.setattr(handlers, "ConversationRepository", _Repo)
    monkeypatch.setattr(handlers, "publish_conversation_event", publish_realtime)
    uow_factory = _UowFactory()
    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow=lambda: uow_factory)),
    )

    await handlers.reconcile_orphaned_agent_runs()

    # Both stale runs were attempted...
    assert finished == [run1, run2]
    # ...but only the one that actually transitioned publishes events.
    assert len(uow_factory.events) == 1
    event = uow_factory.events[0]
    assert isinstance(event, AgentRunCompletedEvent)
    assert event.agent_run_id == run1
    assert event.status == AgentRunStatus.FAILED
    assert [cid for cid, _ in realtime] == [conv1]


@pytest.mark.asyncio
async def test_reconcile_settles_a_conversation_its_run_already_left_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blind spot: a finished run whose conversation stayed active.

    `list_stale_active_runs` asks only about runs, so it cannot see this one —
    the run is terminal, therefore not stale — and nothing else ever will,
    because a terminal run is never finalized again. Dev reported exactly this
    pairing (`status: RUNNING` beside `last_run_status: COMPLETED`), and it
    wedges whatever waits on the conversation.

    The conversation is settled as what the *run* actually did, so a failed run
    is never recorded as a completed conversation.
    """
    completed, failed = uuid4(), uuid4()
    settled: list[tuple[object, object]] = []

    class _Repo:
        def __init__(self, uow) -> None:
            self.uow = uow

        async def list_runs_stuck_stopping(self, *, cutoff_seconds, limit=200):
            return []

        async def list_active_runs_pending_liveness(
            self, *, cutoff_seconds, decided_after_seconds, limit=200
        ):
            return []

        async def list_stale_active_runs(self, *, cutoff_seconds, limit=200):
            return []

        async def list_conversations_stranded_by_a_finished_run(
            self, *, cutoff_seconds, limit=200
        ):
            return [
                StrandedConversationRef(id=completed, run_status="COMPLETED"),
                StrandedConversationRef(id=failed, run_status="FAILED"),
            ]

        async def set_conversation_status(self, *, conversation_id, status):
            settled.append((conversation_id, status))

        def collect_events(self, events: list[object]) -> None:
            self.uow.collect_events(events)

    monkeypatch.setattr(handlers, "ConversationRepository", _Repo)
    monkeypatch.setattr(handlers, "publish_conversation_event", _no_realtime)
    uow_factory = _UowFactory()
    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow=lambda: uow_factory)),
    )

    await handlers.reconcile_orphaned_agent_runs()

    assert settled == [
        (completed, ConversationStatus.COMPLETED),
        (failed, ConversationStatus.FAILED),
    ]


async def _no_realtime(conversation_id, payload) -> None:
    _ = conversation_id, payload


class _ApprovalUowFactory:
    """A unit of work whose repositories are supplied by the test."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(session=None)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _ApprovalRepository:
    def __init__(self, *, conversation, decision) -> None:
        self._conversation = conversation
        self._decision = decision

    async def get_conversation(self, _conversation_id):
        return self._conversation

    async def get_approval_decision(self, **_kwargs):
        return self._decision


class TestApprovalReconciliationJob:
    """The worker half of a durable approval decision."""

    @staticmethod
    def _patch(
        monkeypatch: pytest.MonkeyPatch,
        *,
        conversation,
        decision,
        resolved: list,
        contexts: list,
    ) -> None:
        monkeypatch.setattr(
            handlers,
            "ConversationRepository",
            lambda uow: _ApprovalRepository(
                conversation=conversation, decision=decision
            ),
        )
        monkeypatch.setattr(handlers, "AgentRepository", lambda uow: None)
        monkeypatch.setattr(
            handlers, "create_authorization_data_service", lambda uow: None
        )
        monkeypatch.setattr(handlers, "build_usage_service", lambda uow: None)

        class _Service:
            def __init__(self, **_kwargs) -> None:
                pass

            async def resolve_user_approval_internal(self, **kwargs):
                from app.core.authorization.current import get_current_context

                contexts.append(get_current_context())
                resolved.append(kwargs)

        monkeypatch.setattr(handlers, "ConversationService", _Service)

        class _AuthData:
            async def build_user_context(self, **kwargs):
                return SimpleNamespace(**kwargs)

        monkeypatch.setattr(
            handlers, "create_authorization_data_service", lambda uow: _AuthData()
        )

    def test_the_job_is_registered_on_the_worker(self) -> None:
        """Enqueued by every approval endpoint; an unregistered name would fail
        every one of them at runtime rather than here."""
        from app.core.infrastructure.jobs.streaq_runtime import streaq_worker

        assert "reconcile_agent_approval" in streaq_worker.registry

    @pytest.mark.asyncio
    async def test_it_applies_the_stored_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job carries only identity, so the decision must come from the row
        that was committed — never from anything re-submitted later."""
        conversation = SimpleNamespace(id=uuid4(), user_id=uuid4(), pod_id=uuid4())
        resolved: list = []
        self._patch(
            monkeypatch,
            conversation=conversation,
            decision=("APPROVE_ONCE", {"note": "ok"}),
            resolved=resolved,
            contexts=[],
        )

        await handlers.reconcile_agent_approval_now(
            {
                "conversation_id": str(conversation.id),
                "approval_id": "call-1",
                "user_id": str(conversation.user_id),
                "pod_id": str(conversation.pod_id),
            },
            uow_factory=_ApprovalUowFactory(),
        )

        assert resolved[0]["decision"] == "APPROVE_ONCE"
        assert resolved[0]["response"] == {"note": "ok"}
        assert resolved[0]["approval_id"] == "call-1"

    @pytest.mark.asyncio
    async def test_it_binds_the_owners_authorization_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An approved request_approval runs its wrapped tool with the *user's*
        authority. The request that recorded the decision had a context bound;
        this worker job starts with none, so it must build one or every approved
        tool fails on an absent context."""
        conversation = SimpleNamespace(id=uuid4(), user_id=uuid4(), pod_id=uuid4())
        contexts: list = []
        self._patch(
            monkeypatch,
            conversation=conversation,
            decision=("APPROVE_ONCE", {}),
            resolved=[],
            contexts=contexts,
        )

        await handlers.reconcile_agent_approval_now(
            {
                "conversation_id": str(conversation.id),
                "approval_id": "call-1",
                "user_id": str(conversation.user_id),
                "pod_id": str(conversation.pod_id),
            },
            uow_factory=_ApprovalUowFactory(),
        )

        assert contexts[0].user_id == conversation.user_id
        assert contexts[0].pod_id == conversation.pod_id

    @pytest.mark.asyncio
    async def test_a_missing_decision_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This job can outrun the transaction that recorded the decision, and a
        conversation can be deleted meanwhile. Neither is worth a retry storm."""
        resolved: list = []
        self._patch(
            monkeypatch,
            conversation=SimpleNamespace(id=uuid4(), user_id=uuid4(), pod_id=uuid4()),
            decision=None,
            resolved=resolved,
            contexts=[],
        )

        await handlers.reconcile_agent_approval_now(
            {
                "conversation_id": str(uuid4()),
                "approval_id": "call-1",
                "user_id": str(uuid4()),
                "pod_id": str(uuid4()),
            },
            uow_factory=_ApprovalUowFactory(),
        )

        assert resolved == []
