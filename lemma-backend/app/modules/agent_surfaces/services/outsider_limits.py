"""How often people outside a pod may put its bot to work.

A member's question spends the pod's model budget on the pod's own business. A
stranger's spends it on theirs, in a chat where one person can keep asking, or
where somebody the pod never met added the bot. So there are two ceilings: one
per person per group, which stops one person looping, and one per group per day,
which caps what a whole group can cost.

Fixed windows in Redis, for the reasons ``notification_rate_limiter`` gives. It
fails **closed**, where that limiter fails open: that one guards colleagues, and
losing a message to a colleague is worse than a burst. This guards strangers,
and a stranger going unanswered while Redis is away costs nothing -- their
message is still in the group for a member to answer.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings

logger = get_logger(__name__)

_TEN_MINUTES = 600
_DAY = 86_400


class OutsiderTurnLimiter:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def allow(self, *, group_id: UUID, sender_external_id: str) -> bool:
        """Count this turn and say whether it may run.

        The group's day is charged only for turns that will run. A person over
        their own limit is refused before it, so one person looping cannot use
        up everybody else's allowance in the group.
        """
        client = self._redis or get_redis()
        person_limit = surface_settings.surface_outsider_turns_per_person_per_10_minutes
        group_limit = surface_settings.surface_outsider_turns_per_group_per_day
        try:
            per_person = await incr_with_ttl(
                client, f"outsider:turns:{group_id}:{sender_external_id}", _TEN_MINUTES
            )
            if per_person > person_limit:
                self._refused(group_id, per_person=True)
                return False
            per_group = await incr_with_ttl(client, f"outsider:turns:{group_id}", _DAY)
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.outsider_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        if per_group > group_limit:
            self._refused(group_id, per_person=False)
            return False
        return True

    @staticmethod
    def _refused(group_id: UUID, *, per_person: bool) -> None:
        logger.info(
            "agent_surfaces.outsider_limits.exceeded.observed",
            group_id=str(group_id),
            per_person=per_person,
            per_group=not per_person,
        )


class ContactTurnLimiter:
    """How often contacts may put a bot to work, and how fast it meets new ones.

    The same fixed windows, failing closed, as ``OutsiderTurnLimiter``, for the
    same reason: a contact going unanswered while Redis is away costs nothing,
    and their message is still in the chat for a member to answer.
    """

    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def allow_new_contact(self, *, surface_id: UUID) -> bool:
        """Count one stranger becoming a contact of this bot today."""
        limit = surface_settings.surface_new_contacts_per_surface_per_day
        try:
            count = await incr_with_ttl(
                self._redis or get_redis(), f"contact:new:{surface_id}", _DAY
            )
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.contact_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        if count > limit:
            logger.info(
                "agent_surfaces.contact_limits.new_contacts_exceeded.observed",
                surface_id=str(surface_id),
            )
            return False
        return True

    async def allow(self, *, surface_id: UUID, contact_id: UUID) -> bool:
        """Count this contact's turn and say whether it may run."""
        client = self._redis or get_redis()
        person_limit = surface_settings.surface_contact_turns_per_person_per_10_minutes
        surface_limit = surface_settings.surface_contact_turns_per_surface_per_day
        try:
            per_person = await incr_with_ttl(
                client, f"contact:turns:{surface_id}:{contact_id}", _TEN_MINUTES
            )
            if per_person > person_limit:
                self._refused(surface_id, per_person=True)
                return False
            per_surface = await incr_with_ttl(
                client, f"contact:turns:{surface_id}", _DAY
            )
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.contact_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        if per_surface > surface_limit:
            self._refused(surface_id, per_person=False)
            return False
        return True

    @staticmethod
    def _refused(surface_id: UUID, *, per_person: bool) -> None:
        logger.info(
            "agent_surfaces.contact_limits.exceeded.observed",
            surface_id=str(surface_id),
            per_person=per_person,
        )


#: What one browser address may do with web widgets, whatever the widget.
_SESSIONS_PER_ADDRESS_PER_10_MINUTES = 30
_CODES_PER_ADDRESS_PER_HOUR = 20
_CODES_PER_EMAIL_PER_HOUR = 5
#: Guesses at codes. Each code allows five of its own; these stop a visitor
#: asking for code after code to keep guessing.
_VERIFIES_PER_SESSION_PER_10_MINUTES = 10
_VERIFIES_PER_EMAIL_PER_HOUR = 20
_VERIFIES_PER_ADDRESS_PER_HOUR = 60
#: Reads of a table opened for reads. A page reads once when it opens, so
#: this is generous for a person and still stops one address crawling a key.
_READS_PER_ADDRESS_PER_10_MINUTES = 120
#: Live streams one session may hold open: a tab, and the one replacing it.
_STREAMS_PER_SESSION = 2
_HOUR = 3600

type Window = tuple[str, int, int]


class WebWidgetLimiter:
    """How fast a public key can be used, by anybody holding it.

    Everything here is reachable with nothing but a key copied off a web page,
    so every count fails closed: a visitor refused while Redis is away can try
    again, and a widget nobody can flood is the point.

    Windows are listed narrowest first -- one address, one session -- and a
    refused request gives back what it was charged. So one address hammering a
    widget is stopped by its own window and never spends the widget's day,
    which every other visitor shares.
    """

    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def allow_session(self, *, widget_id: UUID, address: str) -> bool:
        return await self._within(
            (
                f"web:sessions:addr:{address}",
                _TEN_MINUTES,
                _SESSIONS_PER_ADDRESS_PER_10_MINUTES,
            ),
            (
                f"web:sessions:{widget_id}",
                _DAY,
                surface_settings.surface_web_sessions_per_widget_per_day,
            ),
        )

    async def allow_turn(self, *, widget_id: UUID, session_id: UUID) -> bool:
        return await self._within(
            (
                f"web:turns:{widget_id}:{session_id}",
                _TEN_MINUTES,
                surface_settings.surface_contact_turns_per_person_per_10_minutes,
            ),
            (
                f"web:turns:{widget_id}",
                _DAY,
                surface_settings.surface_contact_turns_per_surface_per_day,
            ),
        )

    async def allow_code(self, *, widget_id: UUID, email: str, address: str) -> bool:
        return await self._within(
            (f"web:codes:addr:{address}", _HOUR, _CODES_PER_ADDRESS_PER_HOUR),
            (f"web:codes:email:{widget_id}:{email}", _HOUR, _CODES_PER_EMAIL_PER_HOUR),
        )

    async def allow_verify(self, *, session_id: UUID, email: str, address: str) -> bool:
        return await self._within(
            (
                f"web:verify:session:{session_id}",
                _TEN_MINUTES,
                _VERIFIES_PER_SESSION_PER_10_MINUTES,
            ),
            (f"web:verify:addr:{address}", _HOUR, _VERIFIES_PER_ADDRESS_PER_HOUR),
            (f"web:verify:email:{email}", _HOUR, _VERIFIES_PER_EMAIL_PER_HOUR),
        )

    async def allow_submission(self, *, widget_id: UUID, address: str) -> bool:
        return await self._within(
            (
                f"web:submissions:addr:{address}",
                _TEN_MINUTES,
                _SESSIONS_PER_ADDRESS_PER_10_MINUTES,
            ),
            (
                f"web:submissions:{widget_id}",
                _DAY,
                surface_settings.surface_web_submissions_per_widget_per_day,
            ),
        )

    async def allow_read(self, *, widget_id: UUID, address: str) -> bool:
        return await self._within(
            (
                f"web:reads:addr:{address}",
                _TEN_MINUTES,
                _READS_PER_ADDRESS_PER_10_MINUTES,
            ),
            (
                f"web:reads:{widget_id}",
                _DAY,
                surface_settings.surface_web_reads_per_widget_per_day,
            ),
        )

    async def open_visitor_stream(self, *, session_id: UUID) -> bool:
        """Hold one of the session's stream slots; ``close_visitor_stream`` returns it.

        The key outlives the longest stream, so a slot whose close was lost (a
        process killed mid-stream) frees itself.
        """
        ttl = surface_settings.surface_web_stream_seconds + 60
        return await self._within(
            (f"web:streams:{session_id}", ttl, _STREAMS_PER_SESSION)
        )

    async def close_visitor_stream(self, *, session_id: UUID) -> None:
        client = self._redis or get_redis()
        try:
            await client.decr(f"web:streams:{session_id}")
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.web_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )

    async def _within(self, *windows: Window) -> bool:
        client = self._redis or get_redis()
        charged: list[str] = []
        try:
            for key, ttl, limit in windows:
                count = await incr_with_ttl(client, key, ttl)
                charged.append(key)
                if count > limit:
                    logger.info(
                        "agent_surfaces.web_limits.exceeded.observed",
                        window=key.split(":")[1],
                    )
                    await self._give_back(client, charged)
                    return False
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent_surfaces.web_limits.unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        return True

    @staticmethod
    async def _give_back(client, keys: list[str]) -> None:
        # A refused request did not happen: it leaves every window as it found
        # it, and the window that refused stays full for the next one.
        for key in keys:
            await client.decr(key)
