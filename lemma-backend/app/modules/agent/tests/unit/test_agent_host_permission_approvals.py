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
    "toolCall": {"toolCallId": "call-9", "title": "Run rm -rf build", "kind": "execute"},
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
            {"options": [{"optionId": "x", "kind": "rejectOnce"}, {"optionId": "y", "kind": "allowOnce"}]}
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
    async def test_a_finished_run_reports_undelivered_instead_of_pretending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The repository returns None once the run is terminal. Reporting
        success there would tell the agent an action ran that never did."""
        poked: list = []
        _patch_agent_host(monkeypatch, enqueued={}, command=None, poked=poked)

        result = await agent_host_permission_tool_return(
            request=_request(),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
        )

        assert result["success"] is False
        assert result["executed"] is False
        assert poked == []

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

        result = await agent_host_permission_tool_return(
            request=_request(),
            agent_run_id=uuid7(),
            decision=AgentRunApprovalDecision.DENY,
            response={},
        )

        assert enqueued["option_id"] is None
        assert result["success"] is True
        assert result["executed"] is False


class _FakeUowFactory:
    """A unit of work that touches no database."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(commit=_noop, session=None)

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
        approval_reconciliation, "SessionUnitOfWorkFactory", lambda _maker: _FakeUowFactory()
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
        service.uow = SimpleNamespace(commit=_noop, collect_events=lambda _e: None)

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
        assert dispatched == [
            ("call-9", run_id, AgentRunApprovalDecision.APPROVE_ONCE)
        ]
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
