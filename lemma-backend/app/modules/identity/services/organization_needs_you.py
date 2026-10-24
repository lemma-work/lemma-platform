"""Everything waiting on one member across an organization's pods, as one list.

Three things wait on a person, each kept by the module that owns it:

- a conversation paused on them -- an ``ask_user`` question, a
  ``request_approval``, or an Agent Host permission request (agent);
- a workflow form or decision assigned to them (workflow);
- a contact waiting on a person, in a pod where they may see contacts
  (channels).

Each was a request per pod, so a member of a dozen pods paid three dozen
requests to learn that nothing was waiting. Here each is one statement for
every pod the member may open, and the three are merged oldest first: what has
waited longest is what to do first.

**Every source keeps its own rule.** The pods come from ``load_member_scope``,
the pauses are the member's own conversations, a wait is theirs by being
assigned to their membership, and contacts are read only in pods where their
own context holds ``channel.read`` and ``contact.read`` -- the two the pod's
contact list asks for. Nothing here decides access a second way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversation_summaries import (
    PendingInteractionKind,
    PodPendingInteraction,
    list_pending_interactions_in_pods,
)
from app.modules.channels.contracts.contacts_waiting import (
    ContactWaiting,
    contacts_waiting_on_a_person,
    pods_with_contacts_waiting,
)
from app.modules.identity.services.organization_scope import (
    MemberScope,
    load_member_scope,
)
from app.modules.workflow.contracts.assigned_waits import (
    AssignedWait,
    list_waits_assigned_to,
)

#: A wait the workflow decides on rather than collects a form for.
_DECISION_WAIT = "DECISION"


class NeedsYouKind(str, Enum):
    """What the member is asked for, and so where it is answered."""

    #: A conversation asked them something.
    QUESTION = "QUESTION"
    #: A conversation wants their approval to go on.
    APPROVAL = "APPROVAL"
    #: An agent on an Agent Host asks before using a tool on that computer.
    PERMISSION = "PERMISSION"
    #: A workflow run waits on a form assigned to them.
    FORM = "FORM"
    #: A workflow run waits on a decision assigned to them.
    DECISION = "DECISION"
    #: A contact waits on a person, and nobody holds them.
    CONTACT = "CONTACT"


_PAUSE_KINDS: dict[PendingInteractionKind, NeedsYouKind] = {
    PendingInteractionKind.QUESTION: NeedsYouKind.QUESTION,
    PendingInteractionKind.APPROVAL: NeedsYouKind.APPROVAL,
    PendingInteractionKind.PERMISSION: NeedsYouKind.PERMISSION,
}


@dataclass(frozen=True, slots=True)
class NeedsYouItem:
    """One thing waiting on the member, and where to answer it."""

    #: Stable across reads: ``conversation:<id>``, ``wait:<id>`` or
    #: ``contact:<channel id>``.
    key: str
    kind: NeedsYouKind
    pod_id: UUID
    pod_name: str
    #: What is asked, in the asker's words where there are some.
    title: str | None
    #: Where it is: the conversation, the workflow, why the contact asked.
    detail: str | None
    #: When it started waiting.
    since: datetime
    conversation_id: UUID | None = None
    #: The approval id to resolve a conversation's pause with.
    tool_call_id: str | None = None
    workflow_run_id: UUID | None = None
    channel_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NeedsYou:
    items: list[NeedsYouItem]
    #: More was waiting than ``limit`` items: the oldest are shown.
    truncated: bool


def _text(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def pause_items(
    pauses: Sequence[PodPendingInteraction], pod_names: dict[UUID, str]
) -> list[NeedsYouItem]:
    """One item per conversation, for its longest-waiting pause.

    ``pauses`` arrive longest-waiting first, so the first seen for a
    conversation is the one that says how long the conversation has waited --
    a second question asked since does not make it newer.
    """
    items: dict[UUID, NeedsYouItem] = {}
    for paused in pauses:
        pending = paused.pending
        if pending.conversation_id in items:
            continue
        summary = _text(pending.summary)
        conversation_title = _text(pending.conversation_title)
        items[pending.conversation_id] = NeedsYouItem(
            key=f"conversation:{pending.conversation_id}",
            kind=_PAUSE_KINDS[pending.kind],
            pod_id=paused.pod_id,
            pod_name=pod_names.get(paused.pod_id, ""),
            title=summary or conversation_title,
            detail=conversation_title if summary else None,
            since=pending.created_at,
            conversation_id=pending.conversation_id,
            tool_call_id=pending.tool_call_id,
        )
    return list(items.values())


def wait_items(
    waits: Sequence[AssignedWait], pod_names: dict[UUID, str]
) -> list[NeedsYouItem]:
    return [
        NeedsYouItem(
            key=f"wait:{wait.wait_id}",
            kind=(
                NeedsYouKind.DECISION
                if wait.wait_type == _DECISION_WAIT
                else NeedsYouKind.FORM
            ),
            pod_id=wait.pod_id,
            pod_name=pod_names.get(wait.pod_id, ""),
            title=wait.question or wait.workflow_name,
            detail=wait.workflow_name,
            since=wait.created_at,
            workflow_run_id=wait.run_id,
        )
        for wait in waits
    ]


def contact_items(
    contacts: Sequence[ContactWaiting], pod_names: dict[UUID, str]
) -> list[NeedsYouItem]:
    return [
        NeedsYouItem(
            key=f"contact:{contact.channel_id}",
            kind=NeedsYouKind.CONTACT,
            pod_id=contact.pod_id,
            pod_name=pod_names.get(contact.pod_id, ""),
            title=_text(contact.name),
            detail=_text(contact.reason),
            since=contact.since,
            channel_id=contact.channel_id,
        )
        for contact in contacts
    ]


def oldest_first(items: Sequence[NeedsYouItem], *, limit: int) -> list[NeedsYouItem]:
    """The ``limit`` longest-waiting, oldest first; the key breaks a tie, so
    two reads of the same state list it in the same order."""
    return sorted(items, key=lambda item: (item.since, item.key))[:limit]


async def _pods_seeing_contacts(
    uow: SqlAlchemyUnitOfWork, scope: MemberScope
) -> list[UUID]:
    """The pods with a contact waiting whose contact list this member may read:
    ``channel.read`` for the list, ``contact.read`` for its contacts' channels
    -- each asked of that pod's own context, as the pod's own routes ask them.

    Asked only of pods with somebody waiting. Where the member's role does not
    settle ``contact.read`` the answer costs a grant lookup, and asking every
    pod would put a query per pod back into a read that exists to remove them.
    """
    waiting = await pods_with_contacts_waiting(uow, pod_ids=scope.pod_ids)
    seeing: list[UUID] = []
    for pod_id, ctx in scope.contexts.items():
        if pod_id not in waiting:
            continue
        pod = ResourceRef.pod(pod_id)
        if await ctx.can(Permissions.CHANNEL_READ, pod) and await ctx.can(
            Permissions.CONTACT_READ, pod
        ):
            seeing.append(pod_id)
    return seeing


async def load_needs_you(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, organization_id: UUID, limit: int
) -> NeedsYou | None:
    """What waits on the member across the organization. None for a non-member.

    Each source is asked for up to ``limit`` of its own oldest, so the merged
    list is the oldest across all three, and ``truncated`` says when any of
    them had more than it gave.

    A fixed number of queries whatever the pod count, but for one narrow,
    deliberate exception: ``contact.read`` is decided per pod with the same
    ``ctx.can`` the pod's own contact list uses, only in pods with a contact
    waiting, and where the member's role does not settle it that costs a grant
    lookup.
    """
    scope = await load_member_scope(
        session=uow.session, user_id=user_id, organization_id=organization_id
    )
    if scope is None:
        return None
    pauses, more_pauses = await list_pending_interactions_in_pods(
        uow.session, user_id=user_id, pod_ids=scope.pod_ids, limit=limit
    )
    waits, more_waits = await list_waits_assigned_to(
        uow.session, pod_member_ids=scope.pod_member_ids, limit=limit
    )
    contacts, more_contacts = await contacts_waiting_on_a_person(
        uow, pod_ids=await _pods_seeing_contacts(uow, scope), limit=limit
    )
    names = scope.pod_names
    waiting = [
        *pause_items(pauses, names),
        *wait_items(waits, names),
        *contact_items(contacts, names),
    ]
    return NeedsYou(
        items=oldest_first(waiting, limit=limit),
        truncated=more_pauses or more_waits or more_contacts or len(waiting) > limit,
    )
