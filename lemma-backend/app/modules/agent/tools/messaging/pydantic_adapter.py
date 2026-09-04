"""Messaging toolset: reach a person who is not in this conversation.

The distinction that matters, and the one the docstrings spend their words on:

  ``ask_user``      pauses this turn and resumes it with the answer — but only
                    for the person already here, on the surface already in use.
  ``message_user``  reaches somebody else and returns immediately. Nothing
                    pauses. Their reply is handled by *their* agent, in *their*
                    thread, under *their* permissions, guided by the
                    ``background_instruction`` you write.

Because nothing pauses, an agent that needs answers fires all its messages and
then simply ends its turn. It does not sleep, and it does not poll. Once the
*last* of those asks is answered,
``composition.agent_notifications.deliver_replies_if_settled`` starts the next
turn in the asking conversation, and the agent reads the answers with
``check_messages`` from there.

Waiting for the last one rather than the first is what keeps a four-person
standup to one turn instead of four full conversation replays.

Ending the turn is the whole trick, and it is why this tool is safe to make
non-blocking. A person takes hours; an execution suspended for hours is one
nobody can tell anything, holding a pause that has to be swept if it is ever
abandoned. A finished turn holds nothing.
"""

from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.composition.agent_notifications import (
    check_notifications,
    resolve_recipient,
    send_notification,
)
from app.composition.agent_pod_members import list_pod_members as list_members
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    MAX_TITLE_LENGTH,
    CheckMessagesRequest,
    CheckMessagesResponse,
    ListPodMembersRequest,
    ListPodMembersResponse,
    MessageChannel,
    MessageUserRequest,
    MessageUserResponse,
    NotificationStatusReport,
    PodMemberSummary,
)

logger = get_logger(__name__)

MESSAGE_USER_TOOL_NAME = "message_user"


def _title_for(request: MessageUserRequest) -> str:
    if request.title:
        return request.title.strip()
    first_line = request.message.strip().splitlines()[0]
    if len(first_line) <= MAX_TITLE_LENGTH:
        return first_line
    return first_line[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def _outcome(result: dict, *, requested: MessageChannel | None) -> str:
    """What the model is told happened, in the terms it asked in.

    A refused channel gets its own wording. The generic "no chat app or mailbox
    could carry this" is true of a routing failure and false of this: something
    could have carried it, and the agent needs to see that its own choice is
    what stopped the send — otherwise it retries the same call and reads the
    same sentence.
    """
    reason = result["undeliverable_reason"] or ""
    status = result["delivery_status"]
    if status == "DELIVERED":
        return (
            f"Sent on {result['delivered_via']}. They have not answered yet. "
            "Once you have sent everything you need, end your turn — you get a "
            "fresh one as soon as everyone you asked has replied."
        )
    if status == "UNDELIVERABLE" and requested is not None:
        return (
            f"Not sent: you asked for {requested.value}. {reason} It is in "
            "their Lemma inbox."
        ).strip()
    if status == "UNDELIVERABLE":
        return (
            "No chat app or mailbox could carry this, so it is in their Lemma "
            f"inbox only. {reason}"
        ).strip()
    return (
        "Delivery failed, but the notification exists and is in their Lemma "
        f"inbox. {reason}"
    ).strip()


async def message_user(
    ctx: RunContext[BaseAgentContext], request: MessageUserRequest
) -> MessageUserResponse:
    """Message a pod member who is not in this conversation.

    Reaches them on the chat app they last used, or by email, and always leaves
    a copy in their Lemma inbox.

    It does **not** pause your turn, and their reply never comes back as a tool
    result. Send every message you need, give each a `background_instruction`,
    then **finish your turn and stop**. Do not sleep on it and do not poll: you
    get a fresh turn in this conversation as soon as the last person answers,
    and you read what they said with `check_messages` then.

    Say what you have done and who you are waiting on before you stop. That is
    the last thing whoever asked you sees until the answers land.

    Leave `channel` unset unless you have a reason to pick one — the default is
    already the app they last spoke to you on. When you do name one it is
    honoured or refused, never swapped, so read `reachable_on` from
    `list_pod_members` before choosing.

    To answer the person you are already talking to, just reply — don't use this.
    """
    deps = ctx.deps

    if deps.pod_id is None:
        return MessageUserResponse(
            success=False, error="message_user is only available inside a pod."
        )

    recipient_user_id = await resolve_recipient(
        pod_id=deps.pod_id, reference=request.to
    )
    if recipient_user_id is None:
        return MessageUserResponse(
            success=False,
            error=(
                f"No member of this pod matches '{request.to}'. It takes a pod "
                "member id, user id, or email address — a name will not "
                "resolve. Call list_pod_members to look them up."
            ),
        )

    # No further permission check. Holding this toolset IS the grant to contact
    # colleagues — it is opt-in, withheld from sub-agents, and every message
    # names the human whose authority the run carries. Reaching the run's own
    # owner needs nothing at all: the run already carries their delegated
    # authority. Pod membership is enforced below, in the service, fail-closed.

    # Not wrapped in a try: GracefulToolset already turns a raising tool body
    # into an error result the model can read, and catching here as well would
    # bury a bug in our own code as "the send failed".
    result = await send_notification(
        pod_id=deps.pod_id,
        recipient_user_id=recipient_user_id,
        title=_title_for(request),
        body=request.message,
        actor_user_id=deps.user_id,
        # Normalised, because a delegation token can still name the assistant
        # by the sentinel this module predates -- and that id is not a row in
        # `agents`, while `notifications.actor_agent_id` is a foreign key to
        # that table. Passing it raw fails the insert, and the failure escapes
        # before commit, so the notification row is rolled back too and the
        # recipient gets nothing at all.
        actor_agent_id=effective_agent_id(deps.workload_id, pod_id=deps.pod_id),
        # Display name first, but fall back to the pod-unique name: the display
        # name comes from surface metadata and is None for any run that did not
        # start on a surface -- a schedule, a workflow, the app. That fallback
        # is why this must be normalised: the pod's own agent stores `pod_default`,
        # and this value becomes a chat bot's username and an email `From`.
        agent_name=agent_display_name(deps.agent_display_name or deps.agent_name),
        origin_conversation_id=deps.conversation_id,
        origin_agent_run_id=deps.agent_run_id,
        # Reach out from the surface this run is already on, so the recipient
        # hears from the same bot rather than another of the agent's surfaces.
        origin_surface_id=deps.surface_id,
        # Only when the agent asked. Everything above is a default the router
        # applies; this is an instruction it either follows or refuses.
        channel=request.channel.value if request.channel else None,
        background_instruction=request.background_instruction,
        expects_response=request.expects_response,
        expires_in_seconds=request.expires_in_seconds,
        # A retried worker job replays this exact tool call. Without a key it
        # posts the message twice, and there is no outbound dedup store to
        # catch it.
        idempotency_key=(
            f"run:{deps.agent_run_id}:{ctx.tool_call_id}"
            if deps.agent_run_id and ctx.tool_call_id
            else None
        ),
    )

    return MessageUserResponse(
        success=True,
        message=_outcome(result, requested=request.channel),
        notification_id=result["notification_id"],
        delivery_status=result["delivery_status"],
        delivered_via=result["delivered_via"],
        undeliverable_reason=result["undeliverable_reason"],
    )


async def check_messages(
    ctx: RunContext[BaseAgentContext], request: CheckMessagesRequest
) -> CheckMessagesResponse:
    """Check whether the people you messaged have answered.

    Call it when a turn opens saying they have replied — that is what it is for.
    Not in a loop, and not to check on people who have not answered yet: each
    call replays this whole conversation, and you are told when there is
    something to read. `RESPONDED` is the only status that means somebody
    answered.

    If some are still OPEN after you have genuinely waited, say who has not
    answered and finish with what you have.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return CheckMessagesResponse(
            success=False, error="check_messages is only available inside a pod."
        )
    if not request.notification_ids:
        return CheckMessagesResponse(
            success=False, error="Give at least one notification id."
        )

    reports = await check_notifications(
        pod_id=deps.pod_id, notification_ids=request.notification_ids
    )
    messages = [NotificationStatusReport(**report) for report in reports]
    pending = sum(1 for report in messages if report.status == "OPEN")
    answered = sum(1 for report in messages if report.status == "RESPONDED")

    missing = len(request.notification_ids) - len(messages)
    note = f" {missing} id(s) matched nothing in this pod." if missing > 0 else ""

    return CheckMessagesResponse(
        success=True,
        messages=messages,
        pending=pending,
        message=(
            f"{answered} answered, {pending} still waiting.{note}"
            if messages
            else f"No matching notifications.{note}"
        ),
    )


async def list_pod_members(
    ctx: RunContext[BaseAgentContext], request: ListPodMembersRequest
) -> ListPodMembersResponse:
    """Look up who is in this pod, to find who to `message_user`.

    `message_user` needs an id or an exact email address — a name won't resolve
    — so start here whenever you know a person by name. Each result carries a
    `to` value to pass straight through.

    Also tells you `reachable_on`: the channels that can carry a message to each
    person right now. Read it before setting `message_user`'s `channel`, and
    don't ask for one that isn't listed — the send will be refused rather than
    rerouted.

    Searches names and email addresses; omit `search` to list everyone.
    """
    deps = ctx.deps

    if deps.pod_id is None:
        return ListPodMembersResponse(
            success=False, error="list_pod_members is only available inside a pod."
        )

    result = await list_members(
        pod_id=deps.pod_id,
        requester_user_id=deps.user_id,
        search=request.search,
        limit=request.limit,
        # Whose reach to report. Same sentinel handling as message_user: the pod
        # assistant is not a row in `agents`, and its surfaces are the ones with
        actor_agent_id=effective_agent_id(deps.workload_id, pod_id=deps.pod_id),
    )
    if result is None:
        return ListPodMembersResponse(
            success=False, error="You do not have access to this pod's member list."
        )

    members, total_matched, truncated = result
    if not members:
        detail = (
            f" Nothing matched '{request.search}'."
            if request.search
            else " This pod has no members."
        )
        return ListPodMembersResponse(
            success=True, message=f"No members found.{detail}"
        )

    return ListPodMembersResponse(
        success=True,
        members=[PodMemberSummary(**member) for member in members],
        total_matched=total_matched,
        truncated=truncated,
        message=(
            f"{total_matched} match(es); showing {len(members)}. "
            "Pass a member's `to` value to message_user."
            if truncated
            else f"{len(members)} member(s). Pass a `to` value to message_user."
        ),
    )


messaging_toolset = FunctionToolset[BaseAgentContext](
    tools=[message_user, check_messages, list_pod_members]
)
