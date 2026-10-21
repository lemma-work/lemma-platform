"""Pull inbound email from Resend's received-emails API — no webhook required.

Resend's inbound webhook is push-only, so a runtime without a public HTTPS URL
(the desktop app) never sees replies. This is the pull counterpart, mirroring
the Telegram polling receiver: list the account's received emails, resolve each
to its pod surface by recipient address exactly as the webhook controller does,
and store it through the same acceptor, so the ``surface.ingest`` path fills
the body and runs the agent. The email id is the ``source_event_id`` (minted
as the webhook mints it), so an overlapping poll -- or the webhook delivering
the same mail -- cannot store it twice. The cursor moves in the transaction
that stores the email it passed.
"""

from __future__ import annotations

from app.core.config import reveal_secret
from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    ResendCredentials,
    as_secret,
)

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import resolve_resend_api_key
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.domain.source_event_ids import resend_source_event_id
from app.modules.agent_surfaces.platforms.resend.inbound import (
    normalize_resend_inbound,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.services.resend_recipients import (
    surface_for_recipients,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    ReceiverFailureStreak,
    ReceiverInbox,
    receiver_key,
)

logger = get_logger(__name__)

# Resend has no long-poll; poll on a fixed interval. The page cap bounds a burst
# (or a first run against a large mailbox) so one tick can't fan out unboundedly.
_RESEND_POLL_INTERVAL_SECONDS = 20.0
_RESEND_POLL_PAGE_CAP = 10

# Any: one row of Resend's received-emails list, provider JSON with no shape
# until `normalize_resend_inbound` reads it. Stores it, with the cursor past it.
ResendEmailIngest = Callable[[dict[str, Any]], Awaitable[None]]


def resend_receiver_credentials() -> ResendCredentials | None:
    """System Resend credentials for the poller, or ``None`` if the key is unset."""
    api_key = resolve_resend_api_key()
    if not api_key:
        logger.debug(
            "agent_surfaces.resend_polling_receiver.resend_system_surface_exists_but.diagnostic"
        )
        return None
    return ResendCredentials(api_key=as_secret(api_key))


def resend_candidate_from_surface(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    resend = ResendCredentials.of(credentials)
    api_key = (reveal_secret(resend.api_key) or "").strip()
    if not api_key:
        logger.debug(
            "agent_surfaces.resend_polling_receiver.resend_native_receiver_skipped_surface.diagnostic"
        )
        return None
    # One system key serves every pod address, so all Resend surfaces merge into
    # a single poller keyed by the key. The poller resolves each email's surface
    # by its recipient address, so it does not lean on surface_ids.
    return NativeReceiverCandidate(
        key=receiver_key("resend", "system", api_key),
        platform=SurfacePlatform.RESEND,
        surface_ids=(surface.id,),
        credential_label="system",
        credentials=resend,
    )


def _email_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or "")


def _emails_before_cursor(
    data: list[dict[str, Any]], cursor: str
) -> tuple[list[dict[str, Any]], bool]:
    """The emails on this page newer than ``cursor``, and whether it was reached."""
    fresh: list[dict[str, Any]] = []
    for item in data:
        if _email_id(item) == cursor:
            return fresh, True
        fresh.append(item)
    return fresh, False


class ResendPollingReceiverRunner:
    def __init__(
        self,
        candidate: NativeReceiverCandidate,
        inbox: ReceiverInbox | None = None,
        *,
        ingest: ResendEmailIngest | None = None,
    ) -> None:
        self._candidate = candidate
        self._inbox = inbox or ReceiverInbox(candidate)
        self._ingest = ingest or self._ingest_email
        self._failures = ReceiverFailureStreak(
            platform=candidate.platform,
            credential_label=candidate.credential_label,
        )

    async def run(self) -> None:
        resend = ResendCredentials.of(self._candidate.credentials)
        if not reveal_secret(resend.api_key):
            logger.debug(
                "agent_surfaces.resend_polling_receiver.resend_native_receiver_missing_key.diagnostic"
            )
            return

        service = ResendPlatformService(ResendCredentials(api_key=resend.api_key))
        cursor = await self._inbox.load_cursor(
            legacy=lambda: _load_legacy_resend_cursor(self._candidate.key)
        )

        while True:
            try:
                cursor = await self.poll_once(service, cursor)
                self._failures.succeeded()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures.failed(exc)
            await asyncio.sleep(_RESEND_POLL_INTERVAL_SECONDS)

    async def poll_once(
        self, service: ResendPlatformService, cursor: str | None
    ) -> str | None:
        """Ingest what arrived since ``cursor``; the cursor to resume from.

        The cursor advances one email at a time, in the transaction that
        stores it, so a storing failure stops it just short of that email and
        the next poll retries from there. Storing is a row insert; whatever
        goes wrong *processing* an email is the ingest item's to retry and,
        past its attempts, to record -- it never holds the cursor.
        """
        new_items, newest_id = await self._collect_new_emails(service, cursor)
        if not newest_id or newest_id == cursor:
            return cursor
        if not new_items:
            # The first poll: history is seeded, not replayed.
            await self._inbox.store_cursor(newest_id)
            return newest_id
        reached = cursor
        # Oldest first, so a conversation's messages ingest in order.
        for item in reversed(new_items):
            try:
                await self._ingest(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "agent_surfaces.resend_polling_receiver.email_ingest_failed.degraded",
                    email_id=_email_id(item),
                    exc_info=True,
                )
                break
            reached = _email_id(item) or reached
        return reached

    async def _collect_new_emails(
        self, service: ResendPlatformService, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """New emails since ``cursor`` (top of the list is newest), plus the new
        cursor. ``cursor is None`` seeds silently: real history is not replayed.
        """
        new_items: list[dict[str, Any]] = []
        newest_id: str | None = None
        after: str | None = None
        for _ in range(_RESEND_POLL_PAGE_CAP):
            page = await service.list_received_emails(after=after, limit=100)
            data = page.get("data") or []
            if not data:
                break
            if newest_id is None:
                newest_id = _email_id(data[0]) or None
            if cursor is None:
                break

            fresh, reached_cursor = _emails_before_cursor(data, cursor)
            new_items.extend(fresh)
            if reached_cursor or not page.get("has_more"):
                break
            after = _email_id(data[-1]) or None
            if not after:
                break
        return new_items, newest_id

    async def _ingest_email(self, item: dict[str, Any]) -> None:
        # The list row carries the same fields the webhook envelope does, under
        # ``id`` rather than ``email_id`` — rename so the shared normalizer (and
        # the downstream body enrichment it feeds) can find it.
        data = {**item, "email_id": item.get("id")}
        normalized = normalize_resend_inbound({"data": data})
        recipients = normalized.get("recipients") or []
        email_id = _email_id(item)
        if not recipients:
            await self._inbox.store_cursor(email_id)
            return

        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            surface = await surface_for_recipients(
                SurfaceRepository(uow), normalized, recipients
            )
        if surface is None:
            logger.debug(
                "agent_surfaces.resend_polling_receiver.resend_polling_no_surface_for_address.diagnostic"
            )
            await self._inbox.store_cursor(email_id)
            return

        # Minted by the same helper the webhook uses. A deployment can be
        # running both routes, and only a shared id lets the durable inbox see
        # the two arrivals of one email as one delivery.
        source_event_id = resend_source_event_id(normalized, receiver=str(surface.id))
        if source_event_id is None:
            # Nothing durable to key on, so a redelivery would start a second
            # agent run every time the poller came round. Skipping loses the
            # mail; processing it loses the person's inbox.
            logger.warning(
                "agent_surfaces.resend_polling_receiver.resend_email_has_no_identifier.degraded",
                surface_id=str(surface.id),
            )
            await self._inbox.store_cursor(email_id)
            return
        # Keyed by the surface, as the webhook keys it: a deployment running
        # both routes stores one email once.
        await self._inbox.accept(
            source="resend",
            payload=normalized,
            cursor=email_id,
            surface_id=surface.id,
            source_event_id=source_event_id,
            receiver_scope=str(surface.id),
        )


def _resend_cursor_key(key: str) -> str:
    return f"agent_surfaces:resend_cursor:{key}"


async def _load_legacy_resend_cursor(key: str) -> str | None:
    """The cursor Redis held before receivers kept it in Postgres: read once."""
    redis = get_redis(url=settings.redis_url)
    try:
        raw = await redis.get(_resend_cursor_key(key))
    except Exception:
        logger.warning(
            "agent_surfaces.resend_polling_receiver.could_not_load_resend_cursor.degraded",
            exc_info=True,
        )
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)
