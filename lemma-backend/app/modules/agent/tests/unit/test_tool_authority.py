"""Whose authority an agent tool call runs with is decided in one place.

#597 broke approvals because every tool family built its own delegated context
and the executor relied on stripping a workload id to make them all "act as the
user". These pin the single decision and fail if a tool module goes back to
building its own.
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.agent.domain.context import AgentContext, ApprovedExecution
from app.modules.agent.tools.authority import workload_actor_id, workspace_principal

pytestmark = pytest.mark.unit

_AGENT_MODULE = Path(__file__).resolve().parents[2]
_THE_ONE_PLACE = _AGENT_MODULE / "tools" / "authority.py"


def _context(**overrides) -> AgentContext:
    fields = {
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "conversation_id": uuid4(),
        "agent_name": "chore-agent",
    }
    fields.update(overrides)
    return AgentContext(**fields)


def _approved(ctx: AgentContext) -> AgentContext:
    return ctx.model_copy(
        update={
            "approved_execution": ApprovedExecution(
                approver_user_id=ctx.user_id,
                agent_id=uuid4(),
                conversation_id=ctx.conversation_id,
                tool_name="exec_command",
            )
        }
    )


def test_no_agent_module_builds_its_own_delegated_context() -> None:
    """Every tool gets its authorization context from `tool_authorization_context`.

    A second builder is how an approved call ends up authorised as the agent
    again: it would not know about `ApprovedExecution`.
    """
    offenders = []
    for path in _AGENT_MODULE.rglob("*.py"):
        if "tests" in path.parts or path == _THE_ONE_PLACE:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "build_delegated_workload_context"
            ):
                offenders.append(f"{path.relative_to(_AGENT_MODULE)}:{node.lineno}")
    assert offenders == [], (
        "build the context with app.modules.agent.tools.authority."
        f"tool_authorization_context instead: {offenders}"
    )


def test_an_approved_sandbox_command_runs_in_the_person_s_session() -> None:
    """How an approved `lemma pods delete` in the sandbox gets the person's
    authority: its workspace session is minted for the user, with no workload."""
    agent_id = uuid4()
    ctx = _context().model_copy(
        update={"workload_type": "agent", "workload_id": agent_id}
    )

    assert workspace_principal(_approved(ctx)).workload_id is None
    assert workspace_principal(_approved(ctx)).workload_type is None


def test_an_unapproved_sandbox_command_runs_as_the_agent() -> None:
    agent_id = uuid4()
    ctx = _context().model_copy(
        update={"workload_type": "agent", "workload_id": agent_id}
    )

    principal = workspace_principal(ctx)

    assert (principal.workload_type, principal.workload_id) == ("agent", agent_id)


def test_session_approvals_are_keyed_by_the_agent_s_own_id() -> None:
    agent_id = uuid4()
    ctx = _context().model_copy(update={"workload_id": agent_id})

    assert workload_actor_id(ctx) == f"agent:{agent_id}"


def test_an_approval_cannot_be_edited_after_the_fact() -> None:
    approved = _approved(_context()).approved_execution
    assert approved is not None
    with pytest.raises(ValueError):
        approved.approver_user_id = uuid4()  # type: ignore[misc]
