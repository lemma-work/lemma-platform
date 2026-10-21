"""The people of the pod, for the messaging toolset: who is here, and calling
one of them in.

``list_pod_members`` finds who to write to -- ``channel_post`` takes an id or
an exact email address, never a name. ``ask_a_person`` is the one messaging
tool a run answering somebody outside the pod has: it tells the member who
looks after them, in that member's own channel, and -- in the contact's own
channel -- flags their case as needing a member. A group has no case to flag:
there the note is the call.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.tools import RunContext

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.domain.asked_questions import line_client_id
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    MAX_OUTSIDER_MESSAGE_CHARS,
    AskAPersonRequest,
    AskAPersonResponse,
    ListPodMembersRequest,
    ListPodMembersResponse,
    PodMemberSummary,
)
from app.modules.agent_surfaces.contracts import direct_messages
from app.modules.channels.contracts import (
    NewLine,
    Principal,
    Sender,
    call_for_person,
    direct_channel,
    post,
)
from app.modules.pod.contracts import directory as pod_directory

#: What the member who looks after a contact is told -- where, not what: why
#: is in the contact's channel, which goes with the contact if they are
#: forgotten; a line here would outlive them.
CALLED_IN = (
    "A contact you look after needs a person -- their channel is in Contacts, "
    "waiting on one, with what the bot said."
)
CALLED_IN_GROUP = (
    "Somebody in a group you look after needs a person -- open the group in "
    "Channels to see what they asked and what the bot said."
)


async def ask_a_person(
    ctx: RunContext[BaseAgentContext], request: AskAPersonRequest
) -> AskAPersonResponse:
    """Call a person from the pod into this conversation.

    For when the person you are answering needs something only a member can
    give. The member who looks after them is told, and -- when you are
    answering them in their own channel -- it is flagged as waiting on a
    person; the member will step in and reply here themselves.
    Tell the person someone will get back to them -- do not promise when.
    """
    deps = ctx.deps
    if deps.pod_id is None or deps.conversation_id is None:
        return AskAPersonResponse(success=False, error="Only in a conversation.")
    if not run_audience(deps).answers_outsiders:
        return AskAPersonResponse(
            success=False,
            error="You are talking to a member already: answer them yourself.",
        )
    if deps.keeper_asking:
        # The member who typed this is the person it would call in.
        return AskAPersonResponse(
            success=False,
            error=(
                "This was written in Lemma by the member who looks after this "
                "conversation: answer them here yourself."
            ),
        )
    flagged = await call_a_person_in(
        pod_id=deps.pod_id,
        conversation_id=deps.conversation_id,
        keeper_user_id=deps.user_id,
        reason=request.reason[:MAX_OUTSIDER_MESSAGE_CHARS],
        key=f"{deps.agent_run_id}:{ctx.tool_call_id}"
        if deps.agent_run_id and ctx.tool_call_id
        else None,
    )
    if not flagged:
        return AskAPersonResponse(
            success=False, error="There is nobody to call into this conversation."
        )
    return AskAPersonResponse(
        success=True,
        message="Done: a member has been asked to step in. Tell them someone will reply.",
    )


async def call_a_person_in(
    *,
    pod_id: UUID,
    conversation_id: UUID,
    keeper_user_id: UUID,
    reason: str,
    key: str | None,
) -> bool:
    """The member who looks after the contact hears they need a person, in
    their own channel, and the contact's own channel asks for one (a group's
    has no case to ask in). Whether the conversation is in a channel."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        channel_id = await channel_conversations.channel_of_conversation(
            uow, conversation_id
        )
        if channel_id is None:
            return False
        in_contacts = await call_for_person(
            uow, channel_id=channel_id, reason=reason[:255]
        )
        direct = await direct_channel(uow, pod_id=pod_id, user_id=keeper_user_id)
        await post(
            uow,
            channel_id=direct.id,
            line=NewLine(
                sender=Sender.agent(
                    effective_agent_id(None, pod_id=pod_id),
                    on_behalf_of=Principal.member(keeper_user_id),
                ),
                text=CALLED_IN if in_contacts else CALLED_IN_GROUP,
                client_message_id=line_client_id(f"ask-a-person:{key}")
                if key
                else None,
            ),
        )
        await uow.commit()
    return True


async def list_pod_members(
    ctx: RunContext[BaseAgentContext], request: ListPodMembersRequest
) -> ListPodMembersResponse:
    """Look up who is in this pod, to find who to write to.

    `channel_post` needs an id or an exact email address for `to` — a name won't resolve
    — so start here whenever you know a person by name. Each result carries a
    `to` value to pass straight through, and `reachable_on`: the chat apps
    this pod can reach them on right now.

    Searches names and email addresses; omit `search` to list everyone.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return ListPodMembersResponse(
            success=False, error="list_pod_members is only available inside a pod."
        )
    if deps.audience.answers_outsiders:
        # The directory is read as the member who looks after this run, and
        # everything in it could be repeated to the stranger asking.
        return ListPodMembersResponse(
            success=False,
            error=(
                "You are talking to somebody outside the pod, so its members are "
                "not yours to list. When they need a person, use `ask_a_person`."
            ),
        )
    page = await pod_directory.list_pod_members(
        pod_id=deps.pod_id,
        requester_user_id=deps.user_id,
        search=request.search,
        limit=request.limit,
    )
    if page is None:
        return ListPodMembersResponse(
            success=False, error="You do not have access to this pod's member list."
        )
    members = page.members
    if not members:
        detail = (
            f" Nothing matched '{request.search}'."
            if request.search
            else " This pod has no members."
        )
        return ListPodMembersResponse(
            success=True, message=f"No members found.{detail}"
        )
    reach = await _reachable_on(members, pod_id=deps.pod_id, deps=deps)
    return ListPodMembersResponse(
        success=True,
        members=[
            PodMemberSummary(
                to=member.to,
                name=member.name,
                email=member.email,
                role=member.role,
                is_you=member.is_you,
                # `user_id` is the key reachability is computed against and is
                # deliberately not offered to the model: `to` is the value it
                # copies.
                reachable_on=reach.get(member.user_id, []),
            )
            for member in members
        ],
        total_matched=page.total_matched,
        truncated=page.truncated,
        message=(
            f"{page.total_matched} match(es); showing {len(members)}. "
            "Pass a member's `to` value to channel_post."
            if page.truncated
            else f"{len(members)} member(s). Pass a `to` value to channel_post."
        ),
    )


async def _reachable_on(
    members: tuple[pod_directory.PodDirectoryMember, ...],
    *,
    pod_id: UUID,
    deps: BaseAgentContext,
) -> dict[UUID, list[str]]:
    """Which chat apps reach each listed person, right now -- `pod` says who is
    in the pod, `agent_surfaces` who has a surface. Only for the page returned:
    reachability costs queries per surface."""
    recipients = {member.user_id: member.email for member in members if member.user_id}
    if not recipients:
        return {}
    return await direct_messages.reachable_channels(
        pod_id=pod_id,
        recipients=recipients,
        # The pod assistant is not a row in `agents`; its surfaces carry the
        # pod's own id.
        actor_agent_id=effective_agent_id(deps.workload_id, pod_id=pod_id),
    )


__all__ = [
    "CALLED_IN",
    "CALLED_IN_GROUP",
    "ask_a_person",
    "call_a_person_in",
    "list_pod_members",
]
