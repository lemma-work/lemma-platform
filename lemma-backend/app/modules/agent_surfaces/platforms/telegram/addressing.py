"""Whether a Telegram group message was put to *this* bot.

The parser cannot answer that: an update names the bot only by entities and a
reply's author, and which bot we are is a ``getMe`` away. So the parser records
what it saw and this settles it once the bot's username and id are known.

Pure on purpose, so the rules are testable without Telegram.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.routing_facts import InboundRoutingFacts


def mention_is_provisional(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether a group message might be an @mention of this bot.

    The parser records any @username / text_mention entities but does not set
    `mentioned_agent` for a generic mention -- a `mention` entity is a plain
    @username and does not say *which* user was meant. Settling that costs a
    getMe call, so it is only worth making when the message could plausibly be
    for us, and it has to happen before admission filters the event out.
    """
    if parsed.is_dm:
        return False
    facts = InboundRoutingFacts.of(parsed)
    if facts.reply_to_user_id:
        # A reply to a bot reads as addressed, and which bot is only known
        # once getMe has said who we are.
        return True
    if parsed.mentioned_agent:
        return False
    return bool(
        facts.mentioned_usernames
        or facts.text_mention_user_ids
        or "@" in (parsed.message_text or "")
    )


def addressed_to_this_bot(
    parsed: ParsedInboundSurfaceEvent,
    *,
    bot_username: str,
    bot_user_id: str | None,
) -> ParsedInboundSurfaceEvent:
    """The event with ``mentioned_agent`` settled against this bot's identity.

    Upgraded when the bot is named (an ``@username`` entity, a ``text_mention``
    of its id, or the name typed without an entity) or when the message replies
    to one of its own messages.

    Downgraded when the only reason it looked addressed was a reply to some
    *other* bot. The parser reads any ``reply_to_message.from.is_bot`` as "to
    us", which holds only under privacy mode: a bot that is a group admin, or
    has privacy off, receives every reply, and answered the ones meant for the
    group's other bots.
    """
    facts = InboundRoutingFacts.of(parsed)
    replied_to = facts.reply_to_user_id or ""
    replies_to_us = bool(replied_to) and replied_to == bot_user_id
    if replies_to_us or _names_this_bot(parsed, bot_username, bot_user_id):
        return parsed.model_copy(update={"mentioned_agent": True})
    replies_to_another_bot = (
        bool(replied_to) and bool(bot_user_id) and facts.is_thread_reply
    )
    if replies_to_another_bot and not facts.has_bot_command:
        metadata = {**(parsed.metadata or {}), "is_thread_reply": False}
        return parsed.model_copy(
            update={"mentioned_agent": False, "metadata": metadata}
        )
    return parsed


def _names_this_bot(
    parsed: ParsedInboundSurfaceEvent, bot_username: str, bot_user_id: str | None
) -> bool:
    """An ``@username`` entity, a ``text_mention`` of its id, or the name typed."""
    facts = InboundRoutingFacts.of(parsed)
    username = bot_username.lower()
    if username and (
        username in {name.lower() for name in facts.mentioned_usernames}
        or f"@{username}" in (parsed.message_text or "").lower()
    ):
        return True
    return bool(bot_user_id) and bot_user_id in facts.text_mention_user_ids
