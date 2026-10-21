"""Long-polling Telegram for one bot, when no webhook can be registered.

Telegram allows a single consumer per bot token. The bot's ``surface.receiver``
work item is the lease (one worker holds it; another takes over when its lease
lapses), this polls getUpdates in a loop, and it gives up only after a grace
period of 409s -- long enough for a handover between two workers, short enough
that a bot genuinely owned elsewhere is not held forever. The offset lives in
``surface_receiver_state`` and moves in the transaction that stores the
updates it passed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.core.infrastructure.redis.client import get_redis

from app.core.config import reveal_secret, settings
from app.modules.agent_surfaces.domain.credentials import TelegramCredentials
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.telegram.client import (
    ALLOWED_UPDATES,
    normalize_bot_base_url,
    resolve_api_base,
)
from app.modules.agent_surfaces.platforms.telegram.update_batching import (
    assemble_telegram_updates as _assemble_telegram_updates,
    next_telegram_offset as _next_telegram_offset,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    NativeReceiverConflict,
    NativeReceiverCredentialsRejected,
    ReceiverFailureStreak,
    ReceiverInbox,
)

logger = get_logger(__name__)

_TELEGRAM_CONFLICT_GRACE_SECONDS = 75
# Telegram answers 401 for a revoked token and 404 for a deleted bot: neither
# is fixed by asking again.
_TELEGRAM_REJECTED_TOKEN_STATUSES = frozenset({401, 404})


def _poll_params(offset: int | None) -> dict[str, Any]:
    """Long-poll arguments for getUpdates, resuming from `offset` when there is one."""
    params: dict[str, Any] = {
        "timeout": 30,
        "allowed_updates": json.dumps(ALLOWED_UPDATES),
    }
    if offset is not None:
        params["offset"] = offset
    return params


class TelegramPollingReceiverRunner:
    def __init__(
        self, candidate: NativeReceiverCandidate, inbox: ReceiverInbox | None = None
    ) -> None:
        self._candidate = candidate
        self._inbox = inbox or ReceiverInbox(candidate)
        telegram = TelegramCredentials.of(candidate.credentials)
        # Revealed for the life of the poll loop, which addresses every call
        # to the bot by it.
        self._bot_token = (reveal_secret(telegram.bot_token) or "").strip()
        self._api_base = resolve_api_base(telegram)
        self._failures = ReceiverFailureStreak(
            platform=candidate.platform,
            credential_label=candidate.credential_label,
        )

    async def run(self) -> None:
        if not self._bot_token:
            logger.debug(
                "agent_surfaces.event_receiver_service.telegram_native_receiver_missing_bot.diagnostic"
            )
            return

        base_url = normalize_bot_base_url(self._api_base, self._bot_token)
        offset: int | None = None
        conflict_deadline: float | None = None

        # Long-poll timeout is 30s; give the HTTP read 60s so transient
        # network latency or a slow Telegram response doesn't surface as a
        # noisy ReadTimeout warning. Connect timeout stays short.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=60.0, connect=10.0)
        ) as client:
            await self._telegram_api(
                client,
                base_url,
                "deleteWebhook",
                {"drop_pending_updates": False},
            )
            # Every candidate, the shared system bot included: its key is a
            # fingerprint of its own token like any other, and without a stored
            # offset a restart re-reads whatever Telegram still holds.
            stored = await self._inbox.load_cursor(
                legacy=lambda: _load_legacy_telegram_offset(self._candidate.key)
            )
            offset = int(stored) if stored else None

            while True:
                try:
                    data = await self._telegram_api(
                        client, base_url, "getUpdates", _poll_params(offset)
                    )
                    conflict_deadline = None
                    updates = await self._with_drain(client, base_url, data, offset)
                    for update in _assemble_telegram_updates(updates):
                        offset = await self._dispatch(update, offset)
                    self._failures.succeeded()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    conflict_deadline, keep_polling = await self._recover(
                        exc, conflict_deadline
                    )
                    if not keep_polling:
                        return

    async def _with_drain(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        data: dict[str, Any],
        offset: int | None,
    ) -> list[Any]:
        """The batch, plus a second immediate poll when a message arrived.

        Telegram delivers an album one update at a time, so a message in the
        first batch is a reason to look again straight away: the rest of it is
        usually landing while this one is being read.
        """
        updates = list(data.get("result") or [])
        if not any(isinstance(item.get("message"), dict) for item in updates):
            return updates

        next_offset = _next_telegram_offset(updates, offset)
        await asyncio.sleep(0.45)
        drain_params: dict[str, Any] = {
            "timeout": 0,
            "allowed_updates": json.dumps(ALLOWED_UPDATES),
        }
        if next_offset is not None:
            drain_params["offset"] = next_offset
        drained = await self._telegram_api(
            client,
            base_url,
            "getUpdates",
            drain_params,
        )
        updates.extend(drained.get("result") or [])
        return updates

    async def _dispatch(self, update: dict[str, Any], offset: int | None) -> int | None:
        """Publish one assembled update, and advance the offset past it."""
        update_id = update.get("update_id")
        logger.debug(
            "agent_surfaces.event_receiver_service.telegram_polling_received_update_id.observed",
            update_id=update_id,
        )
        # The update and the offset past it commit together: a crash can
        # neither skip the update nor -- the row is unique on its id -- store
        # it twice.
        next_offset = update_id + 1 if isinstance(update_id, int) else None
        await self._inbox.accept(
            source="telegram",
            payload=update,
            cursor=str(next_offset) if next_offset is not None else None,
        )
        return next_offset if next_offset is not None else offset

    async def _recover(
        self, exc: Exception, conflict_deadline: float | None
    ) -> tuple[float | None, bool]:
        """React to a failed poll: the new deadline, and whether to keep polling.

        A 409 means something else is holding this bot's updates. During a
        handover that is normal for a few seconds, so it is tolerated for a grace
        period and only then read as "someone else owns this bot" and given up.
        """
        status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        )
        if status in _TELEGRAM_REJECTED_TOKEN_STATUSES:
            raise NativeReceiverCredentialsRejected(
                f"Telegram refused this bot's token ({status})"
            ) from exc
        if status == 409:
            now = asyncio.get_running_loop().time()
            if conflict_deadline is None:
                conflict_deadline = now + _TELEGRAM_CONFLICT_GRACE_SECONDS
                logger.debug(
                    "agent_surfaces.event_receiver_service.telegram_polling_hit_409_after.diagnostic"
                )
            if now < conflict_deadline:
                await asyncio.sleep(5)
                return conflict_deadline, True
            # Past the grace period this is not a handover, it is someone
            # else's bot -- most often the same token configured on both a
            # Desktop installation and Lemma Cloud. Returning quietly let the
            # coordinator start the loser again on its next scan, forever,
            # and each attempt steals updates from the consumer that is
            # working. Raising says which of the two this is so the
            # coordinator can stand down.
            raise NativeReceiverConflict(
                "another consumer holds this Telegram bot's updates"
            )

        if isinstance(exc, httpx.ReadTimeout):
            # Expected during long polling: the 30s long-poll can occasionally
            # exceed the read timeout due to network latency. Retry quietly
            # without a noisy traceback.
            logger.debug(
                "agent_surfaces.event_receiver_service.telegram_polling_getupdates_read_timeout.timeout"
            )
            await asyncio.sleep(1)
            return conflict_deadline, True

        self._failures.failed(exc)
        await asyncio.sleep(5)
        return conflict_deadline, True

    async def _telegram_api(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.post(f"{base_url}/{method}", data=params)
        response.raise_for_status()
        return response.json()


def _telegram_offset_key(key: str) -> str:
    return f"agent_surfaces:telegram_offset:{key}"


async def _load_legacy_telegram_offset(key: str) -> str | None:
    """The offset Redis held before receivers kept it in Postgres: read once."""
    redis = get_redis(url=settings.redis_url)
    try:
        raw = await redis.get(_telegram_offset_key(key))
    except Exception:
        logger.warning(
            "agent_surfaces.event_receiver_service.could_not_load_telegram_polling.degraded",
            exc_info=True,
        )
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)
