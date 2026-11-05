"""The collaborators ``PodConnectorCoverageService`` is built from.

Each one is another module's answer, reached through its contract: the pod's
people (``pod``), a member's own authority (the authorizer), a line in a
member's channel (``channels``, the way ``channel_post`` writes one), and the
once-a-day ledger (Redis, so every replica agrees on the day).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from redis.exceptions import RedisError

from app.modules.channels.contracts import (
    ChannelNotFound,
    LineRefused,
    NewLine,
    Principal,
    Sender,
    direct_channel,
    keyed_client_id,
    post,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.services.pod_connector_coverage import MemberNotTold
from app.modules.pod.contracts.people import PodPerson, pod_people

logger = get_logger(__name__)

#: A member is asked about one connector at most once in this long.
ASK_AGAIN_AFTER_SECONDS = 24 * 60 * 60


async def people_through_contracts(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, *, limit: int
) -> list[PodPerson]:
    return await pod_people(uow, pod_id, limit=limit)


async def member_context_through_authorizer(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, pod_id: UUID
) -> Context:
    """The member's authority in the pod, as their own request would hold it."""
    return await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )


async def tell_through_channels(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    asked_by: UUID,
    text: str,
    key: str,
) -> None:
    """A line in the member's own channel, from the pod's agent speaking for
    the editor who asked -- what ``channel_post`` writes when an agent reaches
    a member. Said by the agent rather than by the editor, so the line is not
    one the pod's agent then answers as if the member had written it."""
    pod_agent = effective_agent_id(None, pod_id=pod_id)
    try:
        channel = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
        await post(
            uow,
            channel_id=channel.id,
            line=NewLine(
                sender=Sender.agent(
                    pod_agent,
                    on_behalf_of=Principal.member(asked_by),
                    display_name=agent_display_name(None),
                ),
                text=text,
                client_message_id=keyed_client_id(key, user_id=asked_by),
            ),
        )
    except LineRefused, ChannelNotFound:
        raise MemberNotTold(user_id) from None


class RedisAskLedger:
    """Who was asked about which connector today, in Redis.

    Read before asking, and written only once the line to the member has been
    committed (``PodConnectorCoverageService.ask_members``): a mark set first
    and a post that then failed would leave somebody unasked for a day while
    the editor is told they were asked.
    """

    def __init__(
        self, session: object | None = None, cache: RedisJsonCache | None = None
    ):
        self._session = session
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="connectors:connect-ask",
            ttl_seconds=ASK_AGAIN_AFTER_SECONDS,
        )

    async def asked_today(
        self, *, pod_connector_id: UUID, user_id: UUID
    ) -> bool | None:
        """Whether they were asked in the last day; None while Redis is away.

        None, not False, so nobody is asked while the ledger cannot be read --
        the button promises nobody is asked twice in a day -- and the editor
        is told those people were not asked rather than that they were.
        """
        try:
            async with connection_released(self._session):
                held = await self._cache.get_raw(f"{pod_connector_id}:{user_id}")
        except RedisError, OSError:
            logger.warning(
                "connectors.connect_ask.ledger_unavailable.degraded", exc_info=True
            )
            return None
        return held is not None

    async def mark(self, *, pod_connector_id: UUID, user_id: UUID) -> None:
        """Record that they were asked. A failure is logged and dropped: the
        line was posted, and the worst it costs is a second ask the same day,
        which the channel's per-day message key folds into the first."""
        try:
            async with connection_released(self._session):
                await self._cache.set_raw(f"{pod_connector_id}:{user_id}", "1")
        except RedisError, OSError:
            logger.warning(
                "connectors.connect_ask.ledger_mark_failed.degraded", exc_info=True
            )


def connector_title_through_catalog(
    uow: SqlAlchemyUnitOfWork, auth_configs: AuthConfigRepository
) -> Callable[[UUID], Awaitable[str | None]]:
    """The catalog title of an installation's connector: "Gmail", not `gmail`."""

    async def title(auth_config_id: UUID) -> str | None:
        auth_config = await auth_configs.get(auth_config_id)
        if auth_config is None:
            return None
        titles = await ConnectorRepository(uow=uow).titles_for(
            [auth_config.connector_id]
        )
        return titles.get(auth_config.connector_id)

    return title


__all__ = [
    "ASK_AGAIN_AFTER_SECONDS",
    "RedisAskLedger",
    "connector_title_through_catalog",
    "member_context_through_authorizer",
    "people_through_contracts",
    "tell_through_channels",
]
