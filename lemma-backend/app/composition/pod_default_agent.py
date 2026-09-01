"""The pod's own assistant, minted with the pod it belongs to.

Lives in ``composition`` for the same reason ``agent_email_surface`` does: the
pod module must not import the agent module, so the call is made from here with
a lazy import.

Unlike the mailbox next door, this is **not** best-effort. A pod without a
mailbox is still a perfectly good pod -- it can be talked to in the app, and an
address can be added later. A pod without its assistant's row is broken in a way
that only shows up when somebody tries to use it: the first message would fail
its foreign key on the way in, and the run that answers it has no agent to
resolve. Failing pod creation loudly beats handing back a pod whose only symptom
is that talking to it does not work.
"""

from __future__ import annotations

from uuid import UUID


async def provision_pod_default_agent(uow, *, pod_id: UUID, user_id: UUID) -> None:
    """Create the assistant's row. Raises if it cannot."""
    from app.modules.agent.infrastructure.repositories.agent_repository import (
        AgentRepository,
    )

    await AgentRepository(uow).create_pod_default(pod_id=pod_id, user_id=user_id)
