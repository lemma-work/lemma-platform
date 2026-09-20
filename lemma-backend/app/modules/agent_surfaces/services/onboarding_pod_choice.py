"""Ask a recognised sender which workspace this conversation should use.

Someone who already has an account but no personal route is in an awkward
place: identity recognition knows exactly who they are, and there is still
nowhere for the conversation to go. Falling through to ordinary ingestion told
them to open the website and configure a surface, which is the one thing a
chat-first product should never have to say.

Provisioning silently instead would be worse in a different way -- a single
message would create a pod, and possibly an organization membership, for
someone who never asked for either. So this asks. It offers the workspaces they
already have, newest first, plus the option to name a new one, and only acts on
the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID


from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_routing_sql import (
    routing_surfaces,
)
from app.modules.identity.contracts.organizations import (
    organization_member_ids_for_user,
    preferred_organization_membership,
)
from app.modules.pod.contracts.user_pods import list_attachable_pods

if TYPE_CHECKING:
    from pydantic import JsonValue

#: How many workspaces to offer. Long enough to cover the ones a person
#: actually uses, short enough to read on a phone without scrolling.
MAX_OFFERED_PODS = 5

#: What to type to get a new one. Matched case-insensitively on the first word.
NEW_POD_KEYWORD = "new"


async def candidate_pods(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID | None = None,
    limit: int | None = MAX_OFFERED_PODS,
) -> list[dict[str, JsonValue]]:
    """The workspaces this person could attach the conversation to.

    Shaped as plain JSON because it is stored on the pending row and read back
    when the answer arrives -- what was offered has to survive a restart, and a
    dataclass does not. `limit=None` asks for all of them, which is how the
    answer is re-checked against live access rather than against the handful
    that happened to be shown.

    `organization_id` is the installation's, and passing it is not optional for
    a caller acting for one: routing refuses a pod outside the installation's
    organization, so offering one hands somebody a choice that breaks their
    next message.
    """
    membership_ids = await organization_member_ids_for_user(uow, user_id=user_id)
    pods = await list_attachable_pods(
        session=uow.session,
        organization_member_ids=membership_ids,
        organization_id=organization_id,
        limit=limit,
    )
    return [{"id": str(pod.id), "name": pod.name} for pod in pods]


def offer_text(pods: list[dict[str, JsonValue]]) -> str:
    """The question, as the person reads it."""
    if not pods:
        return (
            "You already have a Lemma account, but no workspace this chat can "
            f"use yet. Reply `{NEW_POD_KEYWORD} <name>` and I will make one -- "
            f"for example `{NEW_POD_KEYWORD} Personal`."
        )
    listed = "\n".join(
        f"{index}. {pod['name']}" for index, pod in enumerate(pods, start=1)
    )
    return (
        "You already have a Lemma account. Which workspace should this chat "
        f"use?\n\n{listed}\n\nReply with a number, or "
        f"`{NEW_POD_KEYWORD} <name>` to start a new one."
    )


class PodChoice:
    """What the reply asked for: an existing workspace, or a new one."""

    __slots__ = ("pod_id", "new_name")

    def __init__(
        self, *, pod_id: UUID | None = None, new_name: str | None = None
    ) -> None:
        self.pod_id = pod_id
        self.new_name = new_name


def read_choice(
    reply: str, offered: list[dict[str, JsonValue]] | None
) -> PodChoice | None:
    """Read the reply against the list that was actually shown.

    Returns None when it says neither -- the caller re-asks rather than
    guessing, because every wrong guess here wires a conversation to the wrong
    workspace.
    """
    text = (reply or "").strip()
    if not text:
        return None
    first, _, rest = text.partition(" ")
    if first.lower() == NEW_POD_KEYWORD:
        name = rest.strip()
        return PodChoice(new_name=name) if name else None
    if text.isdigit():
        index = int(text)
        pods = offered or []
        if 1 <= index <= len(pods):
            return PodChoice(pod_id=UUID(str(pods[index - 1]["id"])))
    return None


async def organization_for_new_pod(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    installation_organization_id: UUID | None,
) -> tuple[UUID, UUID] | None:
    """Where a newly named workspace should live: (organization, membership).

    An installation's organization is a requirement, not a preference. Routing
    refuses a pod outside it, so falling back to some other membership would
    build the person a workspace their next message cannot reach. Without an
    installation -- the shared bot -- there is nothing to be outside of, and the
    oldest membership is as good an answer as any.
    """
    placement = await preferred_organization_membership(
        uow,
        user_id=user_id,
        preferred_organization_id=installation_organization_id,
    )
    if (
        installation_organization_id is not None
        and placement is not None
        and placement[0] != installation_organization_id
    ):
        return None
    return placement


async def has_somewhere_to_talk(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    platform: SurfacePlatform,
    parsed: ParsedInboundSurfaceEvent,
    system_credentials_only: bool,
) -> bool:
    """Is there a surface on this platform this person can actually chat on?

    Deliberately **not** "where would routing send this message". This asked
    routing's selection for a while, on the reasoning that one implementation
    cannot disagree with itself -- but selection answers a different question
    and answers it with, among other things, a surface the sender cannot use.
    It falls back to the thread's existing surface for a non-member precisely so
    ordinary ingestion has somewhere to send the access-denied reply. Reading
    that as "they have somewhere to talk" withheld the workspace choice from a
    person who had just lost access to the only pod they were in -- the exact
    case it exists for.

    What the two paths *do* share is the predicate, and they share it here:
    `routing_surfaces` is the candidate query ingestion runs, and
    `allows_inbound_event` is the per-event filter it applies to the result --
    so a Slack surface belonging to a workspace this installation is not part of
    is excluded here in the same call it is excluded there.

    Scoped to the pods this person belongs to, which is the authorization and
    also the reason this is affordable. Unscoped it reads every surface of the
    platform in the deployment, and a shared-bot sender takes this path on every
    message: the shared destination lives on their preferences and a pod
    surface, not on the identity row, so there is no stored route to short it
    out the way an installation has.

    `system_credentials_only` matches how the transport narrowed the same
    lookup: a message on the shared bot can only be served by a
    system-credential surface, while one on a company's own installation is not
    restricted that way.
    """
    pod_ids = await SqlAlchemySurfaceRoutingResolutionAdapter(uow).get_user_pod_ids(
        user_id
    )
    if not pod_ids:
        return False
    result = await uow.session.execute(
        routing_surfaces(
            platform.value, system_credentials_only=system_credentials_only
        ).where(AgentSurface.pod_id.in_(pod_ids))
    )
    return any(
        surface is not None and surface.allows_inbound_event(parsed)
        for surface in (model.to_entity_or_none() for model in result.scalars().all())
    )
