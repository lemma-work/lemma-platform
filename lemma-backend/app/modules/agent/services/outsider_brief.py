"""The runtime brief for a run answering somebody outside the pod.

The ordinary brief would be wrong here twice over. It names the conversation's
user as "the person you are talking to", and on this run that user is the member
who looks after a group, not the stranger asking in it. And it lists the pod's
tables, files, people and the owner's memory -- names the stranger has no
business learning, handed to a model that is replying to them.

So this says four things and nothing else: which pod the agent is speaking for,
that the person is outside it, who looks after the conversation (by the id
`message_user` takes, so a question can be passed on), and that only Public
things are readable. It is short enough to rebuild every run, so it is not
cached.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)


async def outsider_brief(
    uow_factory: UnitOfWorkFactory, *, pod_id: UUID, owner_user_id: UUID
) -> str:
    """The brief a stranger's run is given in place of the pod's inventory."""
    async with uow_factory() as uow:
        repo = AgentContextBriefRepository(uow)
        pod = await repo.get_pod_profile(pod_id)
        owner = await repo.get_user_profile(owner_user_id)
    return render_outsider_brief(
        pod_name=pod.name, owner_display_name=owner.display_name
    )


def render_outsider_brief(
    *, pod_name: str | None, owner_display_name: str | None
) -> str:
    """The brief's words. Never the owner's email, and never an id: anything in
    this prompt is something a stranger can talk the model into repeating."""
    owner_name = owner_display_name or "the member who looks after this conversation"
    lines = [
        "# Runtime Context",
        f"- You are speaking for the pod {pod_name or '(unnamed)'}.",
        (
            "- The person writing to you is NOT a member of it. Nothing in this "
            "pod is theirs unless it is marked Public."
        ),
        (
            f"- Who looks after this conversation: {owner_name}. Pass on what "
            "you cannot answer with `message_user` -- it always reaches them, "
            "whatever you put in `to` -- and their reply comes back to you here."
        ),
        (
            "- You can read only what the pod has marked Public. A refusal from "
            "a tool is the answer, not an obstacle: say you can't share that here."
        ),
    ]
    return "\n".join(lines)
