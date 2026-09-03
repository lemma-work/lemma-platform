"""Unit tests for sub-agent ownership safety.

The security-critical rule: an agent may only read/control sub-conversations it
actually spawned (parent_id linkage + same user). These tests pin that guard.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.services import subagent_service as subagent_module
from app.modules.agent.services.subagent_service import SubAgentError, SubAgentService

pytestmark = pytest.mark.asyncio


class _FakeUowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(session=SimpleNamespace())

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _deps(conversation_id):
    """A named agent's run context."""
    return SimpleNamespace(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=conversation_id,
        workload_id=uuid4(),
        agent_name="parent",
        agent_run_id=uuid4(),
        is_pod_default_agent=False,
    )


def _patch_repo(monkeypatch, *, conversation, messages=None):
    class _FakeRepo:
        def __init__(self, uow):
            del uow

        async def get_conversation(self, conversation_id, include_runs=False):
            del conversation_id, include_runs
            return conversation

        async def list_messages(
            self, *, conversation_id, after_sequence=None, limit=50
        ):
            del conversation_id, after_sequence, limit
            return list(messages or []), None

    monkeypatch.setattr(subagent_module, "ConversationRepository", _FakeRepo)


async def test_get_messages_rejects_conversation_not_spawned_by_agent(monkeypatch):
    parent_conversation_id = uuid4()
    deps = _deps(parent_conversation_id)
    # A child whose parent_id points at a DIFFERENT conversation -> not owned.
    foreign_child = Conversation(
        id=uuid4(),
        user_id=deps.user_id,
        pod_id=deps.pod_id,
        parent_id=uuid4(),
    )
    _patch_repo(monkeypatch, conversation=foreign_child)

    service = SubAgentService(_FakeUowFactory())

    with pytest.raises(SubAgentError):
        await service.get_messages(deps, conversation_id=foreign_child.id)


async def test_get_messages_returns_messages_for_owned_child(monkeypatch):
    parent_conversation_id = uuid4()
    deps = _deps(parent_conversation_id)
    child = Conversation(
        id=uuid4(),
        user_id=deps.user_id,
        pod_id=deps.pod_id,
        parent_id=parent_conversation_id,  # spawned by this agent's conversation
    )
    sentinel = object()
    _patch_repo(monkeypatch, conversation=child, messages=[sentinel])

    service = SubAgentService(_FakeUowFactory())

    messages = await service.get_messages(deps, conversation_id=child.id)
    assert messages == [sentinel]


# --------------------------------------------------------------------------- #
# Self-spawn + grant enforcement                                               #
# --------------------------------------------------------------------------- #


class _CommittingUowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(session=SimpleNamespace(), commit=self._commit)

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def _commit(self):
        return None


class _CaptureConvService:
    """Captures create_conversation / add_user_message_and_start_run kwargs."""

    def __init__(self):
        self.create_kwargs: dict | None = None
        self.run_kwargs: dict | None = None

    async def create_conversation(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(id=uuid4())

    async def add_user_message_and_start_run(self, **kwargs):
        self.run_kwargs = kwargs
        return SimpleNamespace(conversation_id=uuid4(), agent_run_id=uuid4())


def _patch_spawn(monkeypatch) -> _CaptureConvService:
    captured = _CaptureConvService()
    monkeypatch.setattr(subagent_module, "set_current_context", lambda ctx: "token")
    monkeypatch.setattr(subagent_module, "reset_current_context", lambda token: None)

    async def _fake_agent_ctx(self, uow, deps):
        return None

    monkeypatch.setattr(SubAgentService, "_agent_ctx", _fake_agent_ctx)
    monkeypatch.setattr(
        SubAgentService, "_conversation_service", lambda self, uow: captured
    )
    return captured


def _default_deps(conversation_id):
    """The pod's assistant, as `run_context_builder` builds it.

    It has a row, so it has an id (its pod's) and the reserved name -- the
    absences this double used to carry are the pre-`agents`-row shape.
    """
    pod_id = uuid4()
    return SimpleNamespace(
        user_id=uuid4(),
        pod_id=pod_id,
        conversation_id=conversation_id,
        workload_id=pod_id,
        agent_name=DEFAULT_POD_AGENT_NAME,
        agent_run_id=uuid4(),
        is_pod_default_agent=True,
    )


async def test_self_spawn_without_name_named_parent_bypasses_grant(monkeypatch):
    deps = _deps(uuid4())  # named parent "parent"
    captured = _patch_spawn(monkeypatch)
    service = SubAgentService(_CommittingUowFactory())

    await service.spawn(deps, agent_name=None, input_data={"x": 1})

    # Self-spawn → same agent, grant skipped on both write paths.
    assert captured.create_kwargs["agent_name"] == "parent"
    assert captured.create_kwargs["require_execute_grant"] is False
    assert captured.run_kwargs["require_execute_grant"] is False


async def test_self_spawn_with_own_name_bypasses_grant(monkeypatch):
    deps = _deps(uuid4())
    captured = _patch_spawn(monkeypatch)
    service = SubAgentService(_CommittingUowFactory())

    await service.spawn(deps, agent_name="parent", input_data={})

    assert captured.create_kwargs["agent_name"] == "parent"
    assert captured.create_kwargs["require_execute_grant"] is False


async def test_self_spawn_default_agent_spawns_default_child(monkeypatch):
    deps = _default_deps(uuid4())
    captured = _patch_spawn(monkeypatch)
    service = SubAgentService(_CommittingUowFactory())

    await service.spawn(deps, agent_name=None, input_data={})

    # Default agent self-spawn → another default child (agent_name=None), no grant.
    assert captured.create_kwargs["agent_name"] is None
    assert captured.create_kwargs["require_execute_grant"] is False


async def test_spawn_named_other_agent_enforces_grant(monkeypatch):
    deps = _deps(uuid4())  # named parent "parent"
    captured = _patch_spawn(monkeypatch)
    service = SubAgentService(_CommittingUowFactory())

    await service.spawn(deps, agent_name="other-agent", input_data={})

    # A different named agent must keep the agent.execute grant check.
    assert captured.create_kwargs["agent_name"] == "other-agent"
    assert captured.create_kwargs["require_execute_grant"] is True
    assert captured.run_kwargs["require_execute_grant"] is True


# --------------------------------------------------------------------------- #
# list_children                                                                #
# --------------------------------------------------------------------------- #


async def test_list_children_returns_owned_children_with_status(monkeypatch):
    from app.modules.agent.domain.value_objects import AgentRunStatus

    deps = _deps(uuid4())
    running = SimpleNamespace(
        id=uuid4(),
        agent_id=None,
        title="Running child",
        created_at=None,
        status=None,
        agent_runs=[SimpleNamespace(status=AgentRunStatus.RUNNING)],
    )
    done = SimpleNamespace(
        id=uuid4(),
        agent_id=None,
        title="Done child",
        created_at=None,
        status=None,
        agent_runs=[SimpleNamespace(status=AgentRunStatus.COMPLETED)],
    )
    captured_args: dict = {}

    class _Repo:
        def __init__(self, uow):
            del uow

        async def list_children(self, *, parent_id, user_id, limit=50):
            captured_args.update(parent_id=parent_id, user_id=user_id, limit=limit)
            return [running, done]

    class _AgentRepo:
        def __init__(self, uow):
            del uow

        async def get(self, agent_id):
            return None

    monkeypatch.setattr(subagent_module, "ConversationRepository", _Repo)
    monkeypatch.setattr(subagent_module, "AgentRepository", _AgentRepo)

    service = SubAgentService(_FakeUowFactory())

    rows = await service.list_children(deps)
    assert captured_args["parent_id"] == deps.conversation_id
    assert captured_args["user_id"] == deps.user_id
    statuses = {r["status"] for r in rows}
    assert statuses == {"RUNNING", "COMPLETED"}

    active = await service.list_children(deps, status_filter="ACTIVE")
    assert [r["status"] for r in active] == ["RUNNING"]
