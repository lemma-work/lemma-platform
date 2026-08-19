"""Pull inbound email from Resend's received-emails API — no webhook required.

Resend's inbound webhook is push-only, so a runtime without a public HTTPS URL
(the desktop app) never sees replies. This is the pull counterpart, mirroring
the Telegram polling receiver: list the account's received emails, resolve each
to its pod surface by recipient address exactly as the webhook controller does,
and publish the same ``SurfaceWebhookReceivedEvent`` so the existing enrich →
ingress path fills the body and runs the agent. The email id is the
``source_event_id``, so an overlapping poll cannot double-deliver.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import resolve_resend_api_key
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.resend.inbound import normalize_resend_inbound
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    receiver_key,
)

logger = get_logger(__name__)

# Resend has no long-poll; poll on a fixed interval. The page cap bounds a burst
# (or a first run against a large mailbox) so one tick can't fan out unboundedly.
_RESEND_POLL_INTERVAL_SECONDS = 20.0
_RESEND_POLL_PAGE_CAP = 10


def resend_receiver_credentials() -> dict[str, Any] | None:
    """System Resend credentials for the poller, or ``None`` if the key is unset."""
    api_key = resolve_resend_api_key()
    if not api_key:
        logger.debug('agent_surfaces.resend_polling_receiver.resend_system_surface_exists_but.diagnostic')
        return None
    return {"api_key": api_key}


def resend_candidate_from_surface(
    surface: AgentSurfaceEntity, credentials: dict[str, Any]
) -> NativeReceiverCandidate | None:
    api_key = str(credentials.get("api_key") or "").strip()
    if not api_key:
        logger.debug('agent_surfaces.resend_polling_receiver.resend_native_receiver_skipped_surface.diagnostic')
        return None
    # One system key serves every pod address, so all Resend surfaces merge into
    # a single poller keyed by the key. The poller resolves each email's surface
    # by its recipient address, so it does not lean on surface_ids.
    return NativeReceiverCandidate(
        key=receiver_key("resend", "system", api_key),
        platform=SurfacePlatform.RESEND,
        surface_ids=(surface.id,),
        credential_label="system",
        credentials=credentials,
    )


class ResendPollingReceiverRunner:
    def __init__(self, candidate: NativeReceiverCandidate) -> None:
        self._candidate = candidate

    async def run(self) -> None:
        api_key = str(self._candidate.credentials.get("api_key") or "").strip()
        if not api_key:
            logger.debug('agent_surfaces.resend_polling_receiver.resend_native_receiver_missing_key.diagnostic')
            return

        service = ResendPlatformService({"api_key": api_key})
        cursor = await _load_resend_cursor(self._candidate.key)

        while True:
            try:
                new_items, newest_id = await self._collect_new_emails(service, cursor)
                if newest_id and newest_id != cursor:
                    # Oldest first, so a conversation's messages ingest in order.
                    # On the first run new_items is empty (history is seeded, not
                    # replayed), so this just advances the cursor.
                    for item in reversed(new_items):
                        await self._ingest_email(item)
                    cursor = newest_id
                    await _store_resend_cursor(self._candidate.key, cursor)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug('agent_surfaces.resend_polling_receiver.resend_polling_receiver_error.diagnostic', exc_info=True)
            await asyncio.sleep(_RESEND_POLL_INTERVAL_SECONDS)

    async def _collect_new_emails(
        self, service: ResendPlatformService, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """New emails since ``cursor`` (top of the list is newest), plus the new
        cursor. ``cursor is None`` seeds silently: real history is not replayed.
        """
        new_items: list[dict[str, Any]] = []
        newest_id: str | None = None
        after: str | None = None
        pages = 0
        while pages < _RESEND_POLL_PAGE_CAP:
            page = await service.list_received_emails(after=after, limit=100)
            data = page.get("data") or []
            if not data:
                break
            if newest_id is None:
                newest_id = str(data[0].get("id") or "") or None
            if cursor is None:
                break
            reached_cursor = False
            for item in data:
                if str(item.get("id") or "") == cursor:
                    reached_cursor = True
                    break
                new_items.append(item)
            if reached_cursor or not page.get("has_more"):
                break
            after = str(data[-1].get("id") or "") or None
            if not after:
                break
            pages += 1
        return new_items, newest_id

    async def _ingest_email(self, item: dict[str, Any]) -> None:
        # The list row carries the same fields the webhook envelope does, under
        # ``id`` rather than ``email_id`` — rename so the shared normalizer (and
        # the downstream body enrichment it feeds) can find it.
        data = {**item, "email_id": item.get("id")}
        normalized = normalize_resend_inbound({"data": data})
        recipients = normalized.get("recipients") or []
        if not recipients:
            return

        surface = None
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            repository = SurfaceRepository(uow)
            for address in recipients:
                surface = await repository.get_active_by_address(
                    platform="RESEND", address=address
                )
                if surface is not None:
                    normalized["to"] = address
                    break
        if surface is None:
            logger.debug('agent_surfaces.resend_polling_receiver.resend_polling_no_surface_for_address.diagnostic')
            return

        source_event_id = f"resend:native:{normalized.get('email_id')}"
        event = SurfaceWebhookReceivedEvent(
            event_id=stable_event_id({"event_id": source_event_id}),
            source="resend",
            payload=normalized,
            headers={"x-lemma-surface-event-mode": "native_receiver"},
            surface_id=surface.id,
            source_event_id=source_event_id,
        )
        await EventPublisher.publish(event.stream_name(), event)


def _resend_cursor_key(key: str) -> str:
    return f"agent_surfaces:resend_cursor:{key}"


async def _load_resend_cursor(key: str) -> str | None:
    redis = get_redis(url=settings.redis_url)
    try:
        raw = await redis.get(_resend_cursor_key(key))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:
        logger.debug("agent_surfaces.resend_polling_receiver.could_not_load_resend_cursor.observed", exc_info=True)
        return None


async def _store_resend_cursor(key: str, cursor: str) -> None:
    redis = get_redis(url=settings.redis_url)
    try:
        await redis.set(_resend_cursor_key(key), cursor)
    except Exception:
        logger.debug("agent_surfaces.resend_polling_receiver.could_not_store_resend_cursor.observed", exc_info=True)
