"""A workflow's agent step can start its agent in a checkout of a repository.

A webhook-started run always could: the webhook names the repository and the
branch. A step could not, so a reviewer in a workflow cloned by hand. Now the
step names ``repository`` and ``ref`` (usually expressions over the event), and
the run starts in a worktree of it the way a webhook's does -- or the step
fails, rather than an agent quietly reading the default branch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.application.automation.start import StartAutomatedRun
from app.modules.agent.contracts.automation import _AgentDriver
from app.modules.agent.domain.errors import ConversationValidationError
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.tests.unit.work_recording import RecordingWorkSession
from app.modules.workflow.domain.errors import NodeExecutionError
from app.modules.workflow.domain.nodes import AgentNode
from app.modules.workflow.domain.nodes.agent import AgentNodeConfig
from app.modules.workflow.domain.nodes.bindings import (
    ExpressionInputBinding,
    LiteralInputBinding,
)
from app.modules.workflow.execution.executors.agent import AgentExecutor
from app.modules.workflow.execution.step_context import StepContext

SHA = "0123456789abcdef0123456789abcdef01234567"


def _step(values: dict[str, object]) -> tuple[StepContext, Mock]:
    """A step whose context resolves each binding by its key, and the mock the
    conversation is created through."""
    pod_id = uuid4()
    agent = SimpleNamespace(
        id=uuid4(), pod_id=pod_id, name="reviewer", agent_runtime=None
    )
    starts = StartAutomatedRun(
        Mock(session=RecordingWorkSession()),
        default_runtime=AsyncMock(return_value=None),
        runtime_check=AsyncMock(),
    )
    created = Mock(
        create_conversation=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    starts.conversations = created
    starts.runs = Mock(
        create_agent_run=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    starts.messages = Mock(append_message=AsyncMock())
    starts.organization_of = AsyncMock(return_value=uuid4())  # type: ignore[method-assign]
    adapter = object.__new__(_AgentDriver)
    adapter.agents = Mock(get_by_pod_and_name=AsyncMock(return_value=agent))
    adapter.starts = starts
    step = StepContext(
        run_id=uuid4(),
        flow_id=uuid4(),
        flow_name="pr-review",
        pod_id=pod_id,
        user_id=uuid4(),
        context=SimpleNamespace(
            resolve_inputs=lambda mapping: {key: values.get(key) for key in mapping}
        ),
        agent=adapter,
        function=Mock(),
        work=Mock(),
        agent_access=Mock(require_execute=AsyncMock()),
    )
    return step, created


def _node(*, ref: bool = True) -> AgentNode:
    return AgentNode(
        id="review",
        config=AgentNodeConfig(
            agent_name="reviewer",
            repository=ExpressionInputBinding(
                value="start.payload.repository.full_name"
            ),
            ref=ExpressionInputBinding(value="start.payload.pull_request.head.sha")
            if ref
            else None,
        ),
    )


@pytest.mark.anyio
async def test_the_agent_starts_in_a_checkout_at_the_commit() -> None:
    step, created = _step({"repository": "acme/web", "ref": SHA})

    await AgentExecutor().execute(_node(), step)

    conversation = created.create_conversation.await_args.args[0]
    assert conversation.repo == ProjectRepo(owner="acme", repo="web", ref=SHA)
    # The checkout is the conversation's repo, not a tag beside it.
    assert "repo" not in conversation.metadata


@pytest.mark.anyio
async def test_no_ref_is_the_default_branch() -> None:
    step, created = _step({"repository": "acme/web"})

    await AgentExecutor().execute(_node(ref=False), step)

    conversation = created.create_conversation.await_args.args[0]
    assert conversation.repo == ProjectRepo(owner="acme", repo="web", ref=None)


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["acme", "acme/web/extra", "", None])
async def test_a_name_that_is_not_owner_slash_repo_fails_the_step(name: object) -> None:
    step, created = _step({"repository": name})

    with pytest.raises(NodeExecutionError, match="owner/name"):
        await AgentExecutor().execute(_node(ref=False), step)

    created.create_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_a_name_github_would_not_have_fails_rather_than_runs_without_it() -> None:
    """Shaped like ``owner/name`` but not a name GitHub allows: the agent
    module would drop it and start in an empty folder."""
    step, created = _step({"repository": "-acme/web"})

    with pytest.raises(ConversationValidationError):
        await AgentExecutor().execute(_node(ref=False), step)

    created.create_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_a_ref_a_checkout_cannot_take_fails_rather_than_reading_main() -> None:
    step, created = _step({"repository": "acme/web", "ref": "--upload-pack=x"})

    with pytest.raises(ConversationValidationError, match="ref"):
        await AgentExecutor().execute(_node(), step)

    created.create_conversation.assert_not_awaited()


def test_a_ref_needs_a_repository() -> None:
    with pytest.raises(ValidationError, match="repository"):
        AgentNodeConfig(agent_name="reviewer", ref=LiteralInputBinding(value="main"))
