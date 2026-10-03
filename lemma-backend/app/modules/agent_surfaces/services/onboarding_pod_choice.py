"""Ask a recognised sender which workspace this conversation should use.

Someone who already has an account but no personal route is in an awkward
place: identity recognition knows exactly who they are, and there is still
nowhere for the conversation to go. Falling through to ordinary ingestion told
them to open the website and configure a surface, which is the one thing a
chat-first product should never have to say.

Provisioning silently instead would be worse in a different way -- a single
message would create a pod, and possibly an organization membership, for
someone who never asked for either. So when there is more than one workspace
this asks. It offers the workspaces they already have, newest first, plus the
option to name a new one, and only acts on the answer. (With exactly one there
is nothing to ask, and `onboarding_settle` attaches it.)

The reply is read generously but never guessed at. People answer a numbered
list on a phone keyboard as "2", "2.", "#2", "option 2" or by typing the name,
and each of those is unambiguous; what is not -- a word that matches nothing, a
name two workspaces share -- is answered with what was wrong rather than with
the same question again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.identity.contracts.organizations import (
    organization_member_ids_for_user,
    organization_names,
    preferred_organization_membership,
)
from app.modules.pod.contracts.user_pods import list_attachable_pods

if TYPE_CHECKING:
    from pydantic import JsonValue

#: How many workspaces get a number. Nine keeps every answer one keystroke on a
#: phone and the list readable without scrolling; anybody with more can type a
#: name, which is matched against every workspace they have, not just these.
MAX_OFFERED_PODS = 9

#: How many workspaces are stored with the question, and so can be named in the
#: answer. A bound on the row, not on what a person may pick from.
MAX_STORED_PODS = 50

#: What to type to get a new one. Matched case-insensitively on the first word.
NEW_POD_KEYWORD = "new"

#: "2", "2.", "2)", "#2", "no. 2", "option 2" -- ASCII digits only. `str.isdigit`
#: also accepts "²" and other Unicode digits, and `int()` then raised on them,
#: which crashed the step instead of re-asking.
_NUMBER = re.compile(
    r"^(?:(?:option|number|no\.?|choice|pick)\s*)?#?\s*([0-9]{1,3})\s*[.):!]?$",
    re.IGNORECASE | re.ASCII,
)
_NEW = re.compile(
    rf"^{NEW_POD_KEYWORD}\b\s*[:\-–—]?\s*(.*)$", re.IGNORECASE | re.DOTALL
)


async def candidate_pods(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID | None = None,
    limit: int | None = MAX_STORED_PODS,
) -> list[dict[str, JsonValue]]:
    """The workspaces this person could attach the conversation to.

    Shaped as plain JSON because it is stored on the pending row and read back
    when the answer arrives -- what was offered has to survive a restart, and a
    dataclass does not. `limit=None` asks for all of them, which is how the
    answer is re-checked against live access rather than against the list that
    happened to be stored.

    `organization_id` is the installation's, and passing it is not optional for
    a caller acting for one: routing refuses a pod outside the installation's
    organization, so offering one hands somebody a choice that breaks their
    next message.

    Each entry carries its organization's name, because the commonest list has
    two workspaces both named after the person -- one in each organization they
    belong to -- and "1. Ada, 2. Ada" is not a question anyone can answer.
    """
    membership_ids = await organization_member_ids_for_user(uow, user_id=user_id)
    pods = await list_attachable_pods(
        session=uow.session,
        organization_member_ids=membership_ids,
        organization_id=organization_id,
        limit=limit,
    )
    names = await organization_names(uow, [pod.organization_id for pod in pods])
    return [
        {
            "id": str(pod.id),
            "name": pod.name,
            "organization_name": names.get(pod.organization_id),
        }
        for pod in pods
    ]


def _label(pod: dict[str, JsonValue], repeated: set[str]) -> str:
    name = str(pod["name"])
    organization = pod.get("organization_name")
    if name.casefold() in repeated and organization:
        return f"{name} · {organization}"
    return name


def _repeated_names(pods: list[dict[str, JsonValue]]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for pod in pods:
        key = str(pod["name"]).casefold()
        (repeated if key in seen else seen).add(key)
    return repeated


def offer_text(pods: list[dict[str, JsonValue]], *, lead: str | None = None) -> str:
    """The question, as the person reads it.

    `lead` replaces the opening line when the question is being asked again for
    a reason -- a reply that could not be read, a workspace that refused -- so
    the reason comes first and the list is not prefaced twice.
    """
    if not pods:
        opening = lead or (
            "You already have a Lemma account, but no workspace this chat can use yet."
        )
        return (
            f"{opening} Reply `{NEW_POD_KEYWORD} <name>` and I will make one -- "
            f"for example `{NEW_POD_KEYWORD} Personal`."
        )
    shown = pods[:MAX_OFFERED_PODS]
    repeated = _repeated_names(pods)
    listed = "\n".join(
        f"{index}. {_label(pod, repeated)}" for index, pod in enumerate(shown, start=1)
    )
    more = (
        f"\n\nYou have {len(pods) - len(shown)} more -- reply with the name of "
        "any of them."
        if len(pods) > len(shown)
        else ""
    )
    opening = (
        lead
        or "You already have a Lemma account. Which workspace should this chat use?"
    )
    return (
        f"{opening}\n\n{listed}{more}\n\nReply with a number, or "
        f"`{NEW_POD_KEYWORD} <name>` to start a new one."
    )


@dataclass(frozen=True, slots=True)
class PodChoice:
    """What the reply asked for: an existing workspace, or a new one."""

    pod_id: UUID | None = None
    new_name: str | None = None


@dataclass(frozen=True, slots=True)
class ChoiceProblem:
    """Why a reply could not be read, in words to send back.

    A value rather than ``None`` so the re-ask says what went wrong. The same
    question again, after an answer the person thought was clear, reads as the
    bot not listening.
    """

    message: str


def _range_hint(pods: list[dict[str, JsonValue]]) -> str:
    count = min(len(pods), MAX_OFFERED_PODS)
    if count == 0:
        return f"reply `{NEW_POD_KEYWORD} <name>`"
    if count == 1:
        return f"reply 1, or `{NEW_POD_KEYWORD} <name>`"
    return f"reply 1–{count}, or `{NEW_POD_KEYWORD} <name>`"


def _by_name(
    text: str, pods: list[dict[str, JsonValue]]
) -> PodChoice | ChoiceProblem | None:
    wanted = text.strip().strip(".!?\"'`").casefold()
    if not wanted:
        return None
    repeated = _repeated_names(pods)
    matches = [
        pod
        for pod in pods
        if wanted in (str(pod["name"]).casefold(), _label(pod, repeated).casefold())
    ]
    if len(matches) == 1:
        return PodChoice(pod_id=UUID(str(matches[0]["id"])))
    if matches:
        return ChoiceProblem(
            f"More than one workspace is called {matches[0]['name']} -- "
            "reply with its number instead."
        )
    return None


def read_choice(
    reply: str, offered: list[dict[str, JsonValue]] | None
) -> PodChoice | ChoiceProblem:
    """Read the reply against the list that was actually shown.

    Anything short of one unambiguous workspace is a `ChoiceProblem`: the
    caller re-asks rather than guessing, because every wrong guess here wires a
    conversation to the wrong workspace.
    """
    pods = offered or []
    text = (reply or "").strip()
    unreadable = ChoiceProblem(f"I didn't catch that -- {_range_hint(pods)}.")
    if not text:
        return unreadable
    named = _by_name(text, pods)
    if named is not None:
        # Ahead of the keyword: a workspace actually called "New York trip"
        # is what "new york trip" means, not a new one called "york trip".
        return named
    new = _NEW.match(text)
    if new is not None:
        name = new.group(1).strip()
        if not name:
            return ChoiceProblem(
                f"What should the new workspace be called? Reply "
                f"`{NEW_POD_KEYWORD} <name>`, for example `{NEW_POD_KEYWORD} Personal`."
            )
        return PodChoice(new_name=name[:100])
    number = _NUMBER.match(text)
    if number is not None:
        index = int(number.group(1))
        if 1 <= index <= min(len(pods), MAX_OFFERED_PODS):
            return PodChoice(pod_id=UUID(str(pods[index - 1]["id"])))
        return ChoiceProblem(f"There's no workspace {index} -- {_range_hint(pods)}.")
    return unreadable


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
    receiver_surface_ids: list[UUID] | None,
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
    `list_active_for_routing` is the candidate query ingestion runs, down to the
    `pod_ids` narrowing it now applies for the same reason, and
    `allows_inbound_event` is the per-event filter it applies to the result --
    so a Slack surface belonging to a workspace this installation is not part of
    is excluded here in the same call it is excluded there.

    Scoped to the pods this person belongs to, which is the authorization and
    also the reason this is affordable. Unscoped it reads every surface of the
    platform in the deployment, and a shared-bot sender takes this path on every
    message: the shared destination lives on their preferences and a pod
    surface, not on the identity row, so there is no stored route to short it
    out the way an installation has.

    `system_credentials_only` and `receiver_surface_ids` are how ingestion
    narrows the same lookup, and both are authorization rather than detail. The
    first: a message on the shared bot can only be served by a
    system-credential surface. The second: two installations can share one Slack
    workspace, so the tenant does not say which of them is listening -- without
    it, access through the other company's bot suppressed the question for a
    message their bot will never see. An empty list means "none of them", as it
    does for ingestion.
    """
    pod_ids = await SqlAlchemySurfaceRoutingResolutionAdapter(uow).get_user_pod_ids(
        user_id
    )
    if not pod_ids:
        return False
    candidates = await SurfaceRepository(uow).list_active_for_routing(
        platform.value,
        surface_ids=receiver_surface_ids,
        pod_ids=pod_ids,
        system_credentials_only=system_credentials_only,
    )
    return any(surface.allows_inbound_event(parsed) for surface in candidates)
