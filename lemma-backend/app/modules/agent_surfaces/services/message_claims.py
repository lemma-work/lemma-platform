"""Durable message-level claims, and the dedup store the ingress path is handed.

Two different questions used to share one Redis store:

* "has this *message* been handled?" -- Slack sends ``message`` and
  ``app_mention`` for one message under two event ids, a native button can be
  tapped twice. That is a record, and a lost one answers somebody twice, so it
  lives in Postgres now (``surface_message_claims``), kept 7 days
  (``CLAIMS_KEPT``) -- longer than any platform redelivers.
* "may this stranger be nudged again yet?" -- a throttle, which stays in Redis
  (``RedisStrangerReplyWindow``).

A claim is owned by the inbound row being ingested (``claims_owned_by``), so
that row's own retry -- after a crash, a lease loss -- finds the claim its own
and carries on, where the old Redis claim read the retry as a duplicate and
had to be handed back on every failure path (``release_ingress_claim``).

``SurfaceEventDedupStore`` is the port's implementation: claims on the
caller's session (they commit, or roll back, with the work they guard) or, for
a caller with only a factory, each in its own short transaction.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.infrastructure.adapters.stranger_reply_window import (
    RedisStrangerReplyWindow,
    get_stranger_reply_window,
)
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    MessageClaimRepository,
)

_owner: ContextVar[str | None] = ContextVar("surface_message_claim_owner", default=None)


@contextmanager
def claims_owned_by(owner: str) -> Iterator[None]:
    """Claims taken in this block belong to ``owner`` (an inbound row's id)."""
    token = _owner.set(owner)
    try:
        yield
    finally:
        _owner.reset(token)


def message_claim_key(
    *,
    surface_installation_id: UUID | None,
    platform: str,
    external_channel_id: str | None,
    external_message_id: str,
) -> str:
    surface_key = (
        str(surface_installation_id) if surface_installation_id else "unrouted"
    )
    return (
        f"msg:{platform.lower()}:{surface_key}:{external_channel_id or 'none'}:"
        f"{external_message_id}"
    )


class SurfaceEventDedupStore:
    """Message claims in Postgres, the stranger window in Redis."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork | None = None,
        uow_factory: UnitOfWorkFactory | None = None,
        window: RedisStrangerReplyWindow | None = None,
    ) -> None:
        self._uow = uow
        self._uow_factory = uow_factory
        self._window = window

    @property
    def window(self) -> RedisStrangerReplyWindow:
        return self._window or get_stranger_reply_window()

    async def _claim(self, key: str) -> bool:
        owner = _owner.get()
        if self._uow is not None:
            return await MessageClaimRepository(self._uow.session).claim(
                key, owner=owner
            )
        if self._uow_factory is None:
            raise RuntimeError(
                "SurfaceEventDedupStore needs a session for message claims"
            )
        async with self._uow_factory() as uow:
            return await MessageClaimRepository(uow.session).claim(key, owner=owner)

    async def _release(self, key: str) -> None:
        owner = _owner.get()
        if self._uow is not None:
            await MessageClaimRepository(self._uow.session).release(key, owner=owner)
            return
        if self._uow_factory is None:
            return
        async with self._uow_factory() as uow:
            await MessageClaimRepository(uow.session).release(key, owner=owner)

    async def claim_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> bool:
        del external_thread_id
        if not external_message_id:
            return True
        return await self._claim(
            message_claim_key(
                surface_installation_id=surface_installation_id,
                platform=str(platform),
                external_channel_id=external_channel_id,
                external_message_id=external_message_id,
            )
        )

    async def release_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> None:
        del external_thread_id
        if not external_message_id:
            return
        await self._release(
            message_claim_key(
                surface_installation_id=surface_installation_id,
                platform=str(platform),
                external_channel_id=external_channel_id,
                external_message_id=external_message_id,
            )
        )

    async def claim_stranger_reply(
        self,
        *,
        platform: str,
        surface_installation_id: UUID | None,
        sender_external_user_id: str | None,
    ) -> bool:
        return await self.window.claim_stranger_reply(
            platform=platform,
            surface_installation_id=surface_installation_id,
            sender_external_user_id=sender_external_user_id,
        )

    async def release_stranger_reply(
        self,
        *,
        platform: str,
        surface_installation_id: UUID | None,
        sender_external_user_id: str | None,
    ) -> None:
        await self.window.release_stranger_reply(
            platform=platform,
            surface_installation_id=surface_installation_id,
            sender_external_user_id=sender_external_user_id,
        )


__all__ = [
    "SurfaceEventDedupStore",
    "claims_owned_by",
    "message_claim_key",
]
