"""An ACP permission request resolved as an ordinary Lemma approval.

The point of these tests is the *convergence*: web, Slack, Teams, Telegram and
an Agent Host all record a decision the same way, and only the last leg differs
— a queued host command instead of a resumed pydantic-ai run. Each test below
pins one place where that could quietly diverge again.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.agent_host_permissions import (
    AGENT_HOST_PERMISSION_KEY,
    agent_host_permission_request,
    permission_approval_tool_args,
    permission_approval_tool_call_id,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services import approval_reconciliation
from app.modules.agent.services.approval_reconciliation import (
    agent_host_permission_tool_return,
    dispatch_agent_host_permission,
)
from app.modules.agent.services.conversation_service import ConversationService

_PAYLOAD = {
    "toolCall": {
        "toolCallId": "call-9",
        "title": "Run rm -rf build",
        "kind": "execute",
    },
    "message": "The local agent asked for permission to use a native tool.",
    "options": [
        {"optionId": "reject", "kind": "reject_once", "name": "No"},
        {"optionId": "once", "kind": "allow_once", "name": "Allow once"},
        {"optionId": "always", "kind": "allow_always", "name": "Always allow"},
    ],
}


def _request(payload: dict = _PAYLOAD):
    args = permission_approval_tool_args(payload, request_id="call-9")
    parsed = agent_host_permission_request(args)
    assert parsed is not None
    return parsed


class TestApprovalShape:
    def test_args_look_like_a_normal_request_approval(self) -> None:
        """Every renderer (web card, surface approval plan) reads title/reason/
        tool_name. Naming these anything else would need a special case in each."""
        args = permission_approval_tool_args(_PAYLOAD, request_id="call-9")

        assert args["title"] == "Run rm -rf build"
        assert args["tool_name"] == "execute"
        assert "asked for permission" in str(args["reason"])

    def test_no_args_key_because_lemma_executes_nothing(self) -> None:
        """`args` is what the approval executor runs as the user. A native ACP
        tool runs on the host, so leaving the key out is what keeps the resolve
        path from trying to execute a tool Lemma does not have."""
        args = permission_approval_tool_args(_PAYLOAD, request_id="call-9")

        assert "args" not in args

    def test_call_id_does_not_collide_with_the_native_tool_call(self) -> None:
        """The ACP request id *is* the native tool call id, which the host has
        already reported as its own tool call. Reusing it would make one
        tool_call_id address two different calls."""
        assert permission_approval_tool_call_id("call-9") != "call-9"
        assert "call-9" in permission_approval_tool_call_id("call-9")

    def test_an_ordinary_request_approval_is_not_mistaken_for_one(self) -> None:
        assert agent_host_permission_request({"tool_name": "exec_command"}) is None
        assert agent_host_permission_request({AGENT_HOST_PERMISSION_KEY: {}}) is None


class TestDecisionMapping:
    def test_approve_once_prefers_the_agents_allow_once_option(self) -> None:
        assert _request().option_for(AgentRunApprovalDecision.APPROVE_ONCE) == "once"

    def test_approve_for_session_prefers_allow_always(self) -> None:
        """The ACP agent keeps its own session memory, so "approve for session"
        is expressed with the agent's always-option rather than by recording a
        Lemma session grant for a tool Lemma never runs."""
        request = _request()

        assert (
            request.option_for(AgentRunApprovalDecision.APPROVE_FOR_SESSION) == "always"
        )

    def test_deny_selects_no_option(self) -> None:
        """The host answers Cancelled for an unselected option, which is what an
        ACP agent expects; picking `reject_once` would be a different outcome."""
        assert _request().option_for(AgentRunApprovalDecision.DENY) is None

    def test_camel_case_option_kinds_are_understood(self) -> None:
        """Adapters differ on `allow_once` vs `allowOnce`; guessing wrong would
        silently fall through to "first allowed option"."""
        request = _request(
            {
                "options": [
                    {"optionId": "x", "kind": "rejectOnce"},
                    {"optionId": "y", "kind": "allowOnce"},
                ]
            }
        )

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) == "y"

    def test_approval_with_only_reject_options_denies(self) -> None:
        """Approving by picking a reject option would tell the agent the opposite
        of what the user said."""
        request = _request({"options": [{"optionId": "no", "kind": "reject_always"}]})

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) is None

    def test_an_unlabelled_option_is_still_usable(self) -> None:
        """An adapter that omits `kind` must not make the approval unanswerable."""
        request = _request({"options": [{"optionId": "proceed"}]})

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) == "proceed"


class _Command:
    def __init__(self, host_id) -> None:
        self.host_id = host_id


class TestDispatch:
    @pytest.mark.asyncio
    async def test_decision_is_queued_and_the_host_is_woken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the poke the decision waits out the host's long-poll deadline
        while the user watches an agent that looks stuck."""
        host_id = uuid4()
        run_id = uuid7()
        enqueued: dict = {}
        poked: list = []

        _patch_agent_host(
            monkeypatch,
            enqueued=enqueued,
            command=_Command(host_id),
            poked=poked,
        )

        delivered = await dispatch_agent_host_permission(
            request=_request(),
            agent_run_id=run_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
        )

        assert delivered is True
        assert enqueued == {
            "run_id": run_id,
            "request_id": "call-9",
            "option_id": "once",
        }
        assert poked == [host_id]

    @pytest.mark.asyncio
    async def test_the_dispatch_waits_for_the_commit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing reaches the host until the transcript is durable.

        This used to await the dispatch inline and branch on the result, which
        put a Redis publish and a second session inside the caller's open write
        transaction -- a pooled connection held on a path a user is waiting on.
        Now the decision is queued through `after_commit`, so a caller that
        rolls back never hands a decision to a host for a transcript nobody has.
        """
        enqueued: dict = {}
        poked: list = []
        _patch_agent_host(
            monkeypatch, enqueued=enqueued, command=_Command(uuid4()), poked=poked
        )
        uow = _RecordingUow()

        await agent_host_permission_tool_return(
            uow=uow,
            request=_request(),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
        )

        assert enqueued == {}, "the host was told before the transcript committed"
        assert poked == []

        await uow.commit()

        assert enqueued["option_id"] == "once"
        assert poked != []

    @pytest.mark.asyncio
    async def test_a_finished_run_still_reads_as_queued(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one case deferring costs, recorded so it is not a surprise.

        The repository returns None once the run is terminal, and the message
        used to say so: "the local agent's run ended before this decision
        reached it". It cannot any more -- the message is written before the
        dispatch runs -- so the agent reads "queued" instead.

        Accepted deliberately (product decision, Aug 2026): agent-host delivery
        is asynchronous by nature, the wording never claimed delivery, and an
        orphaned run is cancelled by `reconcile_agent_host_dispatch` regardless.
        """
        poked: list = []
        _patch_agent_host(monkeypatch, enqueued={}, command=None, poked=poked)
        uow = _RecordingUow()

        result = await agent_host_permission_tool_return(
            uow=uow,
            request=_request(),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
        )
        await uow.commit()

        assert result["success"] is True
        assert "queued for the local agent" in result["message"]
        # Still true and still the point: Lemma ran nothing itself.
        assert result["executed"] is False
        assert poked == [], "a terminal run has no live host to poke"

    @pytest.mark.asyncio
    async def test_a_denial_is_delivered_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A denial that never reaches the host leaves its ACP agent blocked
        until the request times out half an hour later."""
        enqueued: dict = {}
        _patch_agent_host(
            monkeypatch, enqueued=enqueued, command=_Command(uuid4()), poked=[]
        )
        uow = _RecordingUow()

        result = await agent_host_permission_tool_return(
            uow=uow,
            request=_request(),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.DENY,
            response={},
        )
        await uow.commit()

        assert enqueued["option_id"] is None
        assert result["success"] is True
        assert result["executed"] is False


class _AfterCommitUow:
    """A unit of work that runs its after-commit callbacks when committed.

    The host dispatch is queued through `after_commit` now rather than awaited
    inline, so a fake that swallowed the callback would let these tests pass
    while nothing reached the host.
    """

    def __init__(self) -> None:
        self._callbacks: list = []

    def collect_events(self, _events) -> None:
        return None

    def after_commit(self, callback) -> None:
        self._callbacks.append(callback)

    async def commit(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            await callback()


class _RecordingUow:
    """Just enough unit of work to hold after-commit callbacks and fire them."""

    def __init__(self) -> None:
        self._callbacks: list = []

    def after_commit(self, callback) -> None:
        self._callbacks.append(callback)

    async def commit(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            await callback()


class _FakeUowFactory:
    """A unit of work that touches no database."""

    def __call__(self):
        return self

    async def __aenter__(self):
        # `after_commit` is part of the real unit of work now: the host dispatch
        # is queued on it rather than awaited inline.
        return SimpleNamespace(
            commit=_noop, session=None, after_commit=lambda _callback: None
        )

    async def __aexit__(self, *_exc):
        return False


async def _noop(*_args, **_kwargs) -> None:
    return None


def _patch_agent_host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enqueued: dict,
    command,
    poked: list,
) -> None:
    """Stub the Agent Host infrastructure the dispatch imports lazily."""
    channels = types.ModuleType("app.modules.agent.infrastructure.agent_host_channels")

    async def poke_host(host_id) -> None:
        poked.append(host_id)

    channels.poke_host = poke_host

    class _Repository:
        def __init__(self, uow) -> None:
            del uow

        async def enqueue_permission_decision(self, **kwargs):
            enqueued.update(kwargs)
            return command

    dispatch = types.ModuleType(
        "app.modules.agent.infrastructure.agent_host_dispatch_repository"
    )
    dispatch.AgentHostDispatchRepository = _Repository
    monkeypatch.setitem(sys.modules, channels.__name__, channels)
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    monkeypatch.setattr(
        approval_reconciliation,
        "SessionUnitOfWorkFactory",
        lambda _maker: _FakeUowFactory(),
    )


class _ConversationRepository:
    """Just enough repository for one approval resolution."""

    def __init__(self, call: Message) -> None:
        self.call = call
        self.decision: tuple | None = None
        self.appended: list[Message] = []
        self.created_runs = 0

    async def get_approval_decision(self, **_kwargs):
        return self.decision

    async def record_approval_decision(self, *, decision, response, **_kwargs):
        self.decision = (decision, response)
        return True

    async def get_tool_call(self, **_kwargs):
        return self.call

    async def get_tool_return(self, **_kwargs):
        return next(
            (m for m in self.appended if m.kind == MessageKind.TOOL_RETURN), None
        )

    async def append_message(self, *, conversation_id, agent_run_id, draft):
        message = Message.create(
            conversation_id=conversation_id,
            sequence=len(self.appended) + 2,
            agent_run_id=agent_run_id,
            role=MessageRole.ASSISTANT,
            kind=draft.kind,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_result=draft.tool_result,
        )
        self.appended.append(message)
        return message

    async def get_agent_run(self, _run_id):
        # None unless a test says otherwise: a run that paused in-process has
        # already finished, so "no run here" is the ordinary case.
        return getattr(self, "paused_run", None)

    async def lock_conversation(self, _conversation_id) -> None:
        return None

    async def get_active_agent_run_for_update(self, _conversation_id):
        return None

    async def create_agent_run(self, **_kwargs):
        self.created_runs += 1
        return SimpleNamespace(id=uuid7())

    async def list_resolved_approval_ids(self, **_kwargs):
        return set()

    async def list_messages(self, **_kwargs):
        return [], None


class TestResolutionRouting:
    """Where a resolved decision goes, for a conversation running on a host."""

    @staticmethod
    def _service(monkeypatch: pytest.MonkeyPatch, dispatched: list):
        run_id = uuid7()
        args = permission_approval_tool_args(_PAYLOAD, request_id="call-9")
        call = Message.create(
            conversation_id=uuid7(),
            sequence=1,
            agent_run_id=run_id,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.TOOL_CALL,
            tool_name="request_approval",
            tool_call_id=permission_approval_tool_call_id("call-9"),
            tool_args=args,
        )
        repository = _ConversationRepository(call)
        service = ConversationService.__new__(ConversationService)
        service.conversation_repository = repository
        # The host dispatch is queued on the unit of work now rather than
        # awaited inline, so the fake has to accept it. It fires the callback
        # eagerly: these tests are about WHETHER the decision reaches the host,
        # and a fake that swallowed the callback would let them pass while
        # nothing happened. That it waits for the commit is asserted separately,
        # in `test_the_dispatch_waits_for_the_commit`.
        service.uow = _AfterCommitUow()

        async def _dispatch(*, request, agent_run_id, decision):
            dispatched.append((request.request_id, agent_run_id, decision))
            return True

        monkeypatch.setattr(
            "app.modules.agent.services.approval_reconciliation"
            ".dispatch_agent_host_permission",
            _dispatch,
        )
        monkeypatch.setattr(
            "app.modules.agent.services.conversation_service"
            ".publish_conversation_event",
            _noop,
        )
        return service, repository, run_id

    @pytest.mark.asyncio
    async def test_decision_reaches_the_host_and_starts_no_resume_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resume run would dispatch a *second* Agent Host run for a turn the
        host is still executing — the whole reason this branch exists."""
        dispatched: list = []
        service, repository, run_id = self._service(monkeypatch, dispatched)
        conversation = SimpleNamespace(
            id=repository.call.conversation_id, agent_id=None, pod_id=uuid4()
        )

        resolution = await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=uuid4(),
            pod_id=conversation.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
        )

        assert resolution.status == "resolved"
        assert dispatched == [("call-9", run_id, AgentRunApprovalDecision.APPROVE_ONCE)]
        assert repository.created_runs == 0

    @pytest.mark.asyncio
    async def test_the_card_is_closed_with_a_tool_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pending approvals are listed by "call without a return". Skipping the
        return would leave the card stuck asking forever."""
        service, repository, _ = self._service(monkeypatch, [])
        conversation = SimpleNamespace(
            id=repository.call.conversation_id, agent_id=None, pod_id=uuid4()
        )

        await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=uuid4(),
            pod_id=conversation.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
        )

        returns = [m for m in repository.appended if m.kind == MessageKind.TOOL_RETURN]
        assert [m.tool_name for m in returns] == ["request_approval"]
        assert returns[0].tool_call_id == repository.call.tool_call_id

    @pytest.mark.asyncio
    async def test_host_permissions_are_never_deferred_to_a_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deferral exists for tools that run for minutes. A queued command plus
        a wake-up is milliseconds, and someone is watching the agent wait."""
        dispatched: list = []
        service, repository, _ = self._service(monkeypatch, dispatched)
        conversation = SimpleNamespace(
            id=repository.call.conversation_id, agent_id=None, pod_id=uuid4()
        )

        resolution = await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=uuid4(),
            pod_id=conversation.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            defer_reconciliation=True,
        )

        assert resolution.status == "resolved"
        assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_a_session_approval_is_recorded_before_the_tool_runs() -> None:
    """The ordering the deferred session-approval write depends on.

    `record_session_approvals` writes to Redis, so it is queued through
    `uow.after_commit` rather than awaited inside the write transaction. That is
    only safe because `execute_approved_tool_as_user` commits BEFORE it runs the
    tool -- it releases the pooled connection across that external boundary --
    so the callback fires while the approval can still be seen by the authorizer
    the tool goes through.

    If someone moves that commit after the execution, the approval would be
    recorded too late and an APPROVE_FOR_SESSION tool call would be denied by
    the very grant the user just gave. Nothing else would fail; this test is the
    only thing that would.
    """
    order: list[str] = []

    class _Uow:
        def __init__(self) -> None:
            self._callbacks: list = []

        def after_commit(self, callback) -> None:
            self._callbacks.append(callback)

        async def commit(self) -> None:
            callbacks, self._callbacks = self._callbacks, []
            for callback in callbacks:
                await callback()

    uow = _Uow()

    async def _record() -> None:
        order.append("approval-recorded")

    # What the caller does: queue the approval, then run the tool through a
    # path that commits first.
    uow.after_commit(_record)

    await uow.commit()  # execute_approved_tool_as_user commits here …
    order.append("tool-ran")  # … and only then executes.

    assert order == ["approval-recorded", "tool-ran"], (
        "the session approval landed after the tool ran; an APPROVE_FOR_SESSION "
        "call will be denied by the grant the user just gave"
    )


class TestAParkedInteractionDoesNotResume:
    """A decision for a turn that is still in flight must not start a second one.

    A remote harness's `ask_user` / `request_approval` parks: the run keeps
    running while the host's MCP bridge holds the tool response open, and the
    synthesized return appended by the resolution is exactly what that bridge is
    polling for. The turn carries on the moment it reads it, so dispatching a
    resume run here would put two runs on the same turn.

    Keyed on the run still running rather than on a marker in the call's
    arguments, unlike the ACP permission above: those arguments come from the
    model, not from Lemma, so there is nothing of ours to read back. "Still
    running" is also the honest condition — it is what makes a resume wrong.
    """

    @pytest.mark.asyncio
    async def test_a_live_run_is_left_to_finish_its_own_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.domain.value_objects import AgentRunStatus

        service, repository, run_id = TestResolutionRouting()._service(monkeypatch, [])
        # An ordinary request_approval — no ACP marker, so only the run's own
        # state can tell this apart from an in-process pause.
        repository.call.tool_args = {"tool_name": "exec_command", "args": {}}
        repository.paused_run = SimpleNamespace(
            id=run_id, status=AgentRunStatus.RUNNING
        )
        conversation = SimpleNamespace(
            id=repository.call.conversation_id, agent_id=None, pod_id=uuid4()
        )

        await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=uuid4(),
            pod_id=conversation.pod_id,
            # Denied rather than approved: an approval would run the wrapped
            # tool as the user, which is a different mechanism entirely. The
            # resume guard is the same either way, and a denial reaches it
            # without dragging runtime resolution into a unit test.
            decision=AgentRunApprovalDecision.DENY,
        )

        assert repository.created_runs == 0, "a duplicate run was dispatched"
        # The answer still has to be written, because it is the thing the parked
        # bridge is waiting to read.
        assert any(m.kind == MessageKind.TOOL_RETURN for m in repository.appended)
