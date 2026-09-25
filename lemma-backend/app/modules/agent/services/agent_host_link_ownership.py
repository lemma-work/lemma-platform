"""Which of a host's links owns it: the one whose ``hello`` claimed the greatest
link generation.

Every accepted ``hello`` takes the host's next ``link_generation`` in the
transaction that authenticates it (``AgentHostLinkStore.open_link``), so two
handshakes racing on different replicas come away with distinct, ordered
values. A link then does two things, in this order, once it is subscribed to
the host's notice channel:

1. reads the current generation, and yields at once if it is already greater
   than its own -- a newer ``hello`` announced before this link was listening;
2. otherwise announces its own generation, and every link holding a smaller one
   yields when it hears it.

Together they are complete for any interleaving: a newer ``hello`` either
claimed before step 1's read, which sees it, or announces after the
subscription, which hears it. And because only a *greater* generation
supersedes, two links that each hear the other's announcement agree on the
winner instead of both closing -- which is what deciding by "any other
connection id" did.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError

from app.core.domain.realtime import RealtimeChannel
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.agent_host.channels import (
    SUPERSEDED,
    HostNotice,
    host_poke_channel,
    superseded_notice,
)
from app.modules.agent.services.agent_host_link_wire import TRANSIENT_ERRORS


logger = get_logger(__name__)


class GenerationReader(Protocol):
    async def link_generation(self, host_id: UUID) -> int | None: ...


class LinkOwnership:
    def __init__(
        self,
        *,
        store: GenerationReader,
        channels: RealtimeChannel,
        connection_id: UUID,
    ) -> None:
        self._store = store
        self._channels = channels
        self._connection_id = connection_id
        #: What this link's ``hello`` claimed; 0 until it has said one.
        self.generation = 0

    def superseded_by(self, notice: HostNotice) -> int | None:
        """The newer generation ``notice`` announces, if it announces one."""
        if (
            notice.kind == SUPERSEDED
            and notice.generation is not None
            and notice.generation > self.generation
        ):
            return notice.generation
        return None

    async def claim(self, host_id: UUID) -> int | None:
        """Steps 1 and 2 above: call once subscribed.

        Returns the newer generation when this link has already been
        superseded, and announces nothing then -- a link on its way out must
        not unseat the one that replaced it.
        """
        try:
            current = await self._store.link_generation(host_id)
        except TRANSIENT_ERRORS:
            # Unknown is not superseded: the announcement still closes anything
            # older, and a newer link's own check closes this one.
            logger.warning(
                "agent.agent_host_link.ownership_check_skipped.degraded",
                host_id=str(host_id),
                exc_info=True,
            )
            current = None
        if current is not None and current > self.generation:
            return current
        await self._announce(host_id)
        return None

    async def _announce(self, host_id: UUID) -> None:
        try:
            await self._channels.publish(
                host_poke_channel(host_id),
                superseded_notice(self._connection_id, self.generation),
            )
        except RedisError, RuntimeError, OSError:
            logger.warning(
                "agent.agent_host_link.announce_skipped.degraded",
                host_id=str(host_id),
                exc_info=True,
            )
