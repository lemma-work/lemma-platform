"""Where an approval or question card was posted, so it can be retired later.

A card outlives the pause it asks about. When the person types something else
instead of tapping, the new turn denies the pause on their behalf -- and the
card used to stay on screen with live Approve and Deny buttons, inviting a tap
that could only be answered "already cancelled". Remembering where each card
went is what lets the platform take its buttons away when that happens.

Keyed by the card's callback id (``conversation_id|tool_call_id``), which every
adapter already holds while rendering. A callback can have several cards -- a
question set goes out as one message per question on some platforms -- so each
key holds a list of handles, appended to without a read.

Best-effort in both directions: a card whose handle could not be stored or
read keeps its buttons, and a tap on it is still answered correctly by the
interaction path. Held for a month: a pause waits as long as the person takes,
and a handle that outlives its card costs a few hundred bytes.
"""

from __future__ import annotations

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.caches import DECISION_CARDS, surface_caches
from app.modules.agent_surfaces.domain.decision_card import DecisionCardHandle

logger = get_logger(__name__)

_STORE_ERRORS = (RedisError, OSError)

#: More cards than any one pause posts; a bound so a runaway renderer cannot
#: grow a key without limit.
_MAX_CARDS_PER_CALLBACK = 20
#: How long the first take of a callback's cards holds it, so a second
#: concurrent take finds nothing rather than retiring every card twice.
_TAKE_CLAIM_TTL_SECONDS = 600


def _cards() -> RedisJsonCache:
    return surface_caches.get(DECISION_CARDS)


def _list_key(callback_id: str) -> str:
    return f"list:{callback_id}"


async def remember_decision_card(callback_id: str, handle: DecisionCardHandle) -> None:
    """Record one more card posted for ``callback_id``. Never raises."""
    if not callback_id:
        return
    try:
        await _cards().ordered_set_add(
            _list_key(callback_id),
            handle.model_dump_json(),
            limit=_MAX_CARDS_PER_CALLBACK,
        )
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.decision_cards.handle_not_stored.degraded",
            platform=handle.platform,
            exc_info=True,
        )


async def take_decision_cards(callback_id: str) -> list[DecisionCardHandle]:
    """Every card posted for ``callback_id``, forgetting them. Never raises.

    Claimed first, so two turns racing to retire the same pause do not both
    edit (or, on WhatsApp, both write under) the same card.
    """
    if not callback_id:
        return []
    cards = _cards()
    key = _list_key(callback_id)
    try:
        if not await cards.set_raw_if_absent(
            f"taken:{callback_id}", "1", ttl_seconds=_TAKE_CLAIM_TTL_SECONDS
        ):
            return []
        stored = await cards.ordered_set_members(key)
        await cards.ordered_set_clear(key)
        legacy = await _take_legacy(cards, callback_id)
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.decision_cards.handle_not_read.degraded", exc_info=True
        )
        return []
    handles = [handle for raw in stored if (handle := _parsed(raw)) is not None]
    return [*legacy, *handles]


async def _take_legacy(
    cards: RedisJsonCache, callback_id: str
) -> list[DecisionCardHandle]:
    """A card remembered before a callback could hold several: one handle,
    stored as JSON under the bare callback id. Read until they expire."""
    try:
        stored = await cards.get_json(callback_id)
    except ValueError:
        return []
    if stored is None:
        return []
    await cards.delete(callback_id)
    try:
        return [DecisionCardHandle.model_validate(stored)]
    except ValidationError:
        return []


def _parsed(raw: str) -> DecisionCardHandle | None:
    try:
        return DecisionCardHandle.model_validate_json(raw)
    except ValidationError:
        return None


__all__ = ["DecisionCardHandle", "remember_decision_card", "take_decision_cards"]
