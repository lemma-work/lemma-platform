"""Messaging toolset: reach a person who is not in this conversation.

The distinction that matters, and the one the docstrings spend their words on:

  ``ask_user``      pauses this turn and resumes it with the answer — but only
                    for the person already here, on the surface already in use.
  ``message_user``  reaches somebody else and returns immediately. Nothing
                    pauses. Their reply is handled by *their* agent, in *their*
                    thread, under *their* permissions, guided by the
                    ``background_instruction`` you write.

Because nothing pauses, an agent that needs answers fires all its messages, then
``snooze``s once for a realistic interval, then calls ``check_messages``. That
loop only works if the model understands the tool does not block, which is why
the docstring says so three different ways.
"""

from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.composition.agent_notifications import (
    check_notifications,
    resolve_recipient,
    send_notification,
    surface_allows_messaging_members,
)
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    MAX_TITLE_LENGTH,
    CheckMessagesRequest,
    CheckMessagesResponse,
    MessageUserRequest,
    MessageUserResponse,
    NotificationStatusReport,
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


async def message_user(
    ctx: RunContext[BaseAgentContext], request: MessageUserRequest
) -> MessageUserResponse:
    """Send a message to a pod member, wherever they actually are.

    Finds them on the chat app they last used — Slack, Telegram, WhatsApp,
    Teams — or emails them, and always leaves a copy in their Lemma inbox. Use
    it to ask a colleague for something, hand work over for review, or tell the
    person whose schedule you are running what you found.

    **This does not pause your turn.** It returns as soon as the message is on
    its way. You will not see their reply as a tool result, ever.

    So getting an answer back is a three-step loop:

    1. Send every message you need, one call each. Give each a
       `background_instruction` saying what counts as an answer and where it
       goes — without one, their reply is just a chat message and nothing comes
       back to you.
    2. `snooze` once, for how long a person realistically takes. Ten minutes if
       they are mid-conversation; an hour or more for a standup. Not a poll loop
       — every wake replays this whole conversation.
    3. `check_messages` with the ids you were given, plus whatever your
       instruction told their agent to write.

    Two things that bite:

    - **`DELIVERED` does not mean answered.** Only `RESPONDED` does. A person can
      read something and do nothing, which is normal.
    - **`UNDELIVERABLE` is not a failure.** It means no chat app or mailbox could
      carry it, and the message is sitting in their Lemma inbox. Say so, and pass
      on `undeliverable_reason` — it usually means they have never messaged the
      bot, which is something a human can fix.

    If you only want to reply to the person you are already talking to, don't use
    this — just answer them.
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
                f"No member of this pod matches '{request.to}'. Use their pod "
                "member id, user id, or email address."
            ),
        )

    # Reaching *someone else* is the act that needs authorization; telling the
    # person whose authority this run already carries is not. A run with no
    # surface (a schedule, a workflow, the web app) has no send policy to
    # consult, so it may only reach its own owner.
    is_self = recipient_user_id == deps.user_id
    if not is_self and not await surface_allows_messaging_members(deps.surface_id):
        return MessageUserResponse(
            success=False,
            error=(
                "This agent is not allowed to message other pod members. A pod "
                "editor can enable it on the surface's send policy (audience: "
                "POD_MEMBERS). You can still message the person this run belongs "
                "to."
            ),
        )

    # Not wrapped in a try: GracefulToolset already turns a raising tool body
    # into an error result the model can read, and catching here as well would
    # bury a bug in our own code as "the send failed".
    result = await send_notification(
        pod_id=deps.pod_id,
        recipient_user_id=recipient_user_id,
        title=_title_for(request),
        body=request.message,
        actor_user_id=deps.user_id,
        actor_agent_id=deps.workload_id,
        agent_name=deps.agent_display_name,
        origin_conversation_id=deps.conversation_id,
        origin_agent_run_id=deps.agent_run_id,
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

    delivery_status = result["delivery_status"]
    if delivery_status == "DELIVERED":
        message = (
            f"Sent on {result['delivered_via']}. They have not answered yet — "
            "check with check_messages after giving them time."
        )
    elif delivery_status == "UNDELIVERABLE":
        message = (
            "No chat app or mailbox could carry this, so it is in their Lemma "
            f"inbox only. {result['undeliverable_reason'] or ''}".strip()
        )
    else:
        message = (
            "Delivery failed, but the notification exists and is in their Lemma "
            f"inbox. {result['undeliverable_reason'] or ''}".strip()
        )

    return MessageUserResponse(
        success=True,
        notification_id=result["notification_id"],
        delivery_status=delivery_status,
        delivered_via=result["delivered_via"],
        undeliverable_reason=result["undeliverable_reason"],
        message=message,
    )


async def check_messages(
    ctx: RunContext[BaseAgentContext], request: CheckMessagesRequest
) -> CheckMessagesResponse:
    """Find out whether the people you messaged have answered.

    Call it after a `snooze`, not in a loop — every call costs a wake and a full
    history replay, and people do not answer faster for being asked twice.

    `RESPONDED` is the only status that means somebody answered; read
    `response_summary` for what they said. `OPEN` means still waiting. Anything
    else means it is over: `EXPIRED` (deadline passed), `ACKNOWLEDGED` (seen,
    nothing owed), `CANCELLED` (withdrawn).

    If several are still OPEN and you have already waited a long time, say so and
    finish with what you have. Do not wait indefinitely on people.
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


messaging_toolset = FunctionToolset[BaseAgentContext](
    tools=[message_user, check_messages]
)
