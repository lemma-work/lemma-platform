"""The runtime brief for a run answering another pod over a link.

A link run is the outsider machinery with the asking pod's grants instead of
nobody's (``domain/outsiders``), and the outsider brief is wrong for it the same
way the ordinary brief is wrong for a stranger: it would describe the people in
a chat, and nobody is in a chat here. What the run needs to know is which pod is
asking, what it can read, that its last message is the answer that pod gets,
and who looks after the link when it cannot answer.

Like the outsider brief it never lists the pod's tables, files or people: those
are names the asking pod has no business learning, and anything here is
something the run can be talked into repeating.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)


async def link_brief(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    asking_pod_id: UUID,
    steward_user_id: UUID,
) -> str:
    """The brief a link run is given in place of the pod's inventory."""
    async with uow_factory() as uow:
        repo = AgentContextBriefRepository(uow)
        pod = await repo.get_pod_profile(pod_id)
        asker = await repo.get_pod_profile(asking_pod_id)
        steward = await repo.get_user_profile(steward_user_id)
    return render_link_brief(
        pod_name=pod.name,
        asking_pod_name=asker.name,
        steward_name=steward.display_name,
    )


def render_link_brief(
    *, pod_name: str | None, asking_pod_name: str | None, steward_name: str | None
) -> str:
    """The brief's words. No ids and no addresses, for the outsider brief's reason."""
    here = pod_name or "this pod"
    asker = asking_pod_name or "another pod"
    keeper = steward_name or "the member who looks after this link"
    lines = [
        "# Runtime Context",
        f"- You are speaking for the pod {here}.",
        (
            f"- {asker}, another pod in your organization, is asking you over a "
            f"link {here}'s people set up. Its request is the message here; "
            "nobody from it is in this conversation."
        ),
        (
            f"- You can read what {here} has marked Public and what it shared "
            f"with {asker}, and nothing else. You cannot change anything. A "
            "refusal from a tool is the answer, not an obstacle."
        ),
        (
            f"- Your final message goes back to {asker} as your answer, and "
            f"anyone working with {asker} may read it. Answer from what you can "
            "read, and say plainly what you could not see."
        ),
        (
            f"- Who looks after this link: {keeper}. Pass on what you cannot "
            "answer with `message_user` -- it always reaches them -- and their "
            "reply comes back to you here."
        ),
    ]
    return "\n".join(lines)
