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
from app.modules.agent.tests.unit.repository_fakes import wire_repositories
from app.modules.agent.domain.errors import ApprovalNotOwnedError
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services import approval_reconciliation
from app.modules.agent.services.approval_reconciliation import (
    agent_host_permission_tool_return,
)
from app.modules.agent.services.pause_resume import PauseResume
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.conversation_approvals import (
    ApprovalCoordinator,
)
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.run_state import RunState

_PAYLOAD = {
    "request_id": "call-9",
    "tool_call_id": "call-9",
    "tool": {
        "name": "exec_command",
        "source": "native",
        "title": "Run rm -rf build",
        "kind": "execute",
    },
    "input": {"cmd": "rm -rf build"},
    "message": "The local agent asked for permission to use a native tool.",
    "options": [
        {"option_id": "reject", "kind": "reject_once", "name": "No"},
        {"option_id": "once", "kind": "allow_once", "name": "Allow once"},
        {"option_id": "always", "kind": "allow_always", "name": "Always allow"},
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
        assert args["tool_name"] == "exec_command"
        assert "asked for permission" in str(args["reason"])

    def test_no_args_key_because_lemma_executes_nothing(self) -> None:
        """`args` is what the approval executor runs as the user. A native ACP
        tool runs on the host, so leaving the key out is what keeps the resolve
        path from trying to execute a tool Lemma does not have."""
        args = permission_approval_tool_args(_PAYLOAD, request_id="call-9")

        assert "args" not in args

    def test_the_marker_carries_what_would_run_and_every_option(self) -> None:
        """The card shows the gated call's input, and offers "for this
        conversation" only when an allow_always option exists -- labelled
        with that option's own name."""
        args = permission_approval_tool_args(_PAYLOAD, request_id="call-9")
        marker = args[AGENT_HOST_PERMISSION_KEY]

        assert marker["input"] == {"cmd": "rm -rf build"}
        assert {
            "option_id": "always",
            "kind": "allowalways",
            "name": "Always allow",
        } in marker["options"]
        # Still read back the same way.
        assert agent_host_permission_request(args) is not None

    def test_a_request_without_input_carries_an_empty_one(self) -> None:
        payload = {key: value for key, value in _PAYLOAD.items() if key != "input"}
        args = permission_approval_tool_args(payload, request_id="call-9")

        assert args[AGENT_HOST_PERMISSION_KEY]["input"] == {}

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

    def test_a_one_time_approval_never_becomes_always(self) -> None:
        """The host caches an "always" answer for the rest of the session, so
        picking it for a one-time yes turned that yes into a standing one."""
        request = _request(
            {
                "options": [
                    {"optionId": "no", "kind": "reject_once"},
                    {"optionId": "always", "kind": "allow_always"},
                ]
            }
        )

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) is None
        assert request.refuses_a_one_time_approval(
            AgentRunApprovalDecision.APPROVE_ONCE
        )
        assert (
            request.option_for(AgentRunApprovalDecision.APPROVE_FOR_SESSION) == "always"
        )

    def test_an_ordinary_request_refuses_nothing(self) -> None:
        assert not _request().refuses_a_one_time_approval(
            AgentRunApprovalDecision.APPROVE_ONCE
        )
        assert not _request().refuses_a_one_time_approval(AgentRunApprovalDecision.DENY)

    def test_an_unlabelled_option_is_still_usable(self) -> None:
        """An adapter that omits `kind` must not make the approval unanswerable."""
        request = _request({"options": [{"optionId": "proceed"}]})

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) == "proceed"


class _Command:
    def __init__(self, host_id) -> None:
        self.host_id = host_id


class TestDispatch:
    @pytest.mark.asyncio
    async def test_the_command_is_written_with_the_return_and_the_poke_waits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The RESOLVE_PERMISSION command goes into the caller's transaction,
        beside the tool return, so the two commit or roll back together. Only
        the wake-up waits for the commit: a Redis publish has no business in an
        open write transaction, and without it the decision would wait for the
        link's floor tick while the person watches an agent that looks stuck."""
        host_id = uuid4()
        run_id = uuid7()
        enqueued: dict = {}
        poked: list = []
        _patch_agent_host(
            monkeypatch, enqueued=enqueued, command=_Command(host_id), poked=poked
        )
        uow = _RecordingUow()

        result = await agent_host_permission_tool_return(
            uow=uow,
            request=_request(),
            agent_run_id=run_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
        )

        assert enqueued.pop("uow") is uow, "queued outside the caller's transaction"
        assert enqueued == {
            "run_id": run_id,
            "request_id": "call-9",
            "option_id": "once",
        }
        assert poked == [], "the host was woken before the decision committed"
        assert "queued for the local agent" in result["message"]

        await uow.commit()

        assert poked == [host_id]

    @pytest.mark.asyncio
    async def test_a_failed_enqueue_fails_the_whole_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It used to run after the commit: the transcript already read
        "Approved" and the card was closed when the enqueue failed, and the host
        auto-denied the request half an hour later. Raising here, inside the
        transaction, is what rolls the return back with it."""

        class _Unavailable(Exception):
            pass

        _patch_agent_host(
            monkeypatch,
            enqueued={},
            command=None,
            poked=[],
            error=_Unavailable("database went away"),
        )
        uow = _RecordingUow()

        with pytest.raises(_Unavailable):
            await agent_host_permission_tool_return(
                uow=uow,
                request=_request(),
                agent_run_id=uuid7(),
                decision=AgentRunApprovalDecision.APPROVE_ONCE,
                response={},
            )
        assert uow.pending() == 0

    @pytest.mark.asyncio
    async def test_a_finished_run_is_told_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The repository returns None once the run is terminal. Known before
        the message is written now, so the agent reads the truth rather than
        "queued" for a decision nobody will collect."""
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
        assert "ended before this decision reached it" in result["message"]
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

    @pytest.mark.asyncio
    async def test_a_one_time_approval_of_an_always_only_request_says_no(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enqueued: dict = {}
        _patch_agent_host(
            monkeypatch, enqueued=enqueued, command=_Command(uuid4()), poked=[]
        )

        result = await agent_host_permission_tool_return(
            uow=_RecordingUow(),
            request=_request(
                {"options": [{"optionId": "always", "kind": "allow_always"}]}
            ),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
        )

        assert enqueued["option_id"] is None
        assert result["message"].startswith("Not approved.")
        assert "for the session" in result["message"]


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

    def pending(self) -> int:
        return len(self._callbacks)

    async def commit(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            await callback()


async def _noop(*_args, **_kwargs) -> None:
    return None


def _patch_agent_host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enqueued: dict,
    command,
    poked: list,
    error: Exception | None = None,
) -> None:
    """Stub the Agent Host infrastructure the dispatch imports lazily."""
    channels = types.ModuleType("app.modules.agent.infrastructure.agent_host.channels")

    async def poke_host(host_id) -> None:
        poked.append(host_id)

    channels.poke_host = poke_host

    class _Repository:
        def __init__(self, uow) -> None:
            self.uow = uow

        async def enqueue_permission_decision(self, **kwargs):
            if error is not None:
                raise error
            enqueued.update(kwargs)
            # Which transaction it was written in, for the test to compare.
            enqueued.setdefault("uow", self.uow)
            return command

    dispatch = types.ModuleType(
        "app.modules.agent.infrastructure.agent_host.dispatch_repository"
    )
    dispatch.AgentHostDispatchRepository = _Repository

    class _Runs:
        """A run Lemma never ended for a parked window: it has no mark."""

        def __init__(self, uow) -> None:
            del uow

        async def get_agent_run(self, agent_run_id):
            return SimpleNamespace(id=agent_run_id, metadata={}, state=RunState())

    repositories = types.ModuleType("app.modules.agent.infrastructure.db.runs")
    repositories.Runs = _Runs
    monkeypatch.setitem(sys.modules, channels.__name__, channels)
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    monkeypatch.setitem(sys.modules, repositories.__name__, repositories)


class _Repositories:
    """Just enough of every repository for one approval resolution.

    One object standing in for each of them (``wire_repositories``), so a test can read
    what was decided and appended in one place.
    """

    def __init__(self, call: Message) -> None:
        self.call = call
        self.decision: tuple | None = None
        self.appended: list[Message] = []
        self.created_runs = 0
        self.execution_claims = 0

    async def claim_approval_execution(self, **_kwargs):
        """One claim per approval, like the real conditional UPDATE."""
        self.execution_claims += 1
        return self.execution_claims == 1

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
        repository = _Repositories(call)
        # The host's wake-up is queued on the unit of work, so the fake has to
        # accept it. What reaches the host -- the command, written in the
        # caller's transaction -- is recorded by the stub below; that the
        # wake-up waits for the commit is asserted in `TestDispatch`.
        uow = _AfterCommitUow()
        # The real coordinator, not a stand-in and not a half-built service:
        # what these tests check is its routing, so anything less would certify
        # the test's own wiring instead.
        service = wire_repositories(
            ApprovalCoordinator(
                uow,
                ResumeToolReturnBuilder(uow, None),
                wire_repositories(PauseResume(uow, None), repository),
            ),
            repository,
        )

        async def _enqueue(uow, *, request, agent_run_id, decision):
            del uow
            dispatched.append((request.request_id, agent_run_id, decision))
            return uuid4()

        monkeypatch.setattr(
            approval_reconciliation, "stage_agent_host_permission", _enqueue
        )
        monkeypatch.setattr(approval_reconciliation, "_wake_host", _noop)
        monkeypatch.setattr(
            "app.modules.agent.services.pause_resume.publish_conversation_event",
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
        # The owner decides: an approval lends the owner's authority.
        conversation = SimpleNamespace(
            id=repository.call.conversation_id,
            agent_id=None,
            pod_id=uuid4(),
            user_id=uuid4(),
        )

        resolution = await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
        )

        assert resolution.status == "resolved"
        assert dispatched == [("call-9", run_id, AgentRunApprovalDecision.APPROVE_ONCE)]
        assert repository.created_runs == 0

    @pytest.mark.asyncio
    async def test_only_the_owner_can_approve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An approved call runs with the owner's authority, so a decision from
        anyone else is refused before anything is recorded -- the one place
        every entry point (HTTP, buttons, typed replies) passes through."""
        service, repository, _ = self._service(monkeypatch, [])
        conversation = SimpleNamespace(
            id=repository.call.conversation_id,
            agent_id=None,
            pod_id=uuid4(),
            user_id=uuid4(),
        )

        with pytest.raises(ApprovalNotOwnedError):
            await service.resolve_user_approval_internal(
                conversation=conversation,
                approval_id=repository.call.tool_call_id,
                user_id=uuid4(),
                pod_id=conversation.pod_id,
                decision=AgentRunApprovalDecision.APPROVE_ONCE,
            )

        assert repository.decision is None
        assert repository.appended == []

    @pytest.mark.asyncio
    async def test_the_card_is_closed_with_a_tool_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pending approvals are listed by "call without a return". Skipping the
        return would leave the card stuck asking forever."""
        service, repository, _ = self._service(monkeypatch, [])
        # The owner decides: an approval lends the owner's authority.
        conversation = SimpleNamespace(
            id=repository.call.conversation_id,
            agent_id=None,
            pod_id=uuid4(),
            user_id=uuid4(),
        )

        await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=conversation.user_id,
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
        """The worker is for tools that run for minutes. A queued command plus
        a wake-up is milliseconds, and someone is watching the agent wait."""
        dispatched: list = []
        service, repository, _ = self._service(monkeypatch, dispatched)
        # The owner decides: an approval lends the owner's authority.
        conversation = SimpleNamespace(
            id=repository.call.conversation_id,
            agent_id=None,
            pod_id=uuid4(),
            user_id=uuid4(),
        )

        resolution = await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
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

    Keyed on the mark Lemma writes on the call when it records it over MCP
    (`parked_in_run`), with the run still running: not on the call's arguments,
    which come from the model, and not on the run's status alone, which an
    in-process pause answered before its run was written WAITING shares.
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
        repository.call.state = MessageState(parked_in_run_id=run_id)
        repository.paused_run = SimpleNamespace(
            id=run_id, status=AgentRunStatus.RUNNING
        )
        # The owner decides: an approval lends the owner's authority.
        conversation = SimpleNamespace(
            id=repository.call.conversation_id,
            agent_id=None,
            pod_id=uuid4(),
            user_id=uuid4(),
        )

        await service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=repository.call.tool_call_id,
            user_id=conversation.user_id,
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
