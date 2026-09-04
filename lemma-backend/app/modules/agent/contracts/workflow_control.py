"""How `workflow` gets an agent driver.

One factory, and it is the exception to "operations, not classes": `AgentPort`
is a port with four methods that the engine holds for the length of a run, so
publishing four free functions would only make the caller reassemble them.

The class it returns is still not published -- the caller names the port, not
the implementation, and cannot reach past it to a repository.

A submodule for the same reason as its siblings in `schedule` and `connectors`:
this reaches the repository layer, and `contracts/__init__` is imported by
anything that wants any contract at all.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.adapters.workflow_control import (
    AgentControlAdapter,
)
from app.modules.workflow.contracts import AgentPort


def build_agent_control_adapter(uow: SqlAlchemyUnitOfWork) -> AgentPort:
    """An agent driver bound to this transaction."""
    return AgentControlAdapter(uow)


__all__ = ["build_agent_control_adapter"]
