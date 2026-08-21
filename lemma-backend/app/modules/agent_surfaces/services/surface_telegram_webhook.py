"""Registering and revoking a Telegram bot's webhook.

Telegram allows exactly one webhook per bot token, so a surface changing its
account or event mode has to give the old registration up before the new one is
made -- and the two halves of that decision are made together, in
:func:`_telegram_transition`, rather than each looking at the surface alone.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID


from app.core.config import settings
from app.modules.agent_surfaces.platforms.common import (
    public_https_api_url_available,
)
from app.modules.agent_surfaces.platforms.delivery import with_retry
from app.modules.agent_surfaces.platforms.telegram.client import (
    ALLOWED_UPDATES,
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.telegram.mode import (
    telegram_requires_webhook_setup,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfacePlatformError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    ensure_unique_telegram_account,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy

logger = get_logger(__name__)

# Bounded retry for the in-process Telegram webhook registration calls.
_WEBHOOK_RETRY_POLICY = RetryPolicy(max_attempts=3, base_delay=0.5)

if TYPE_CHECKING:
    pass


@dataclass(frozen=True, slots=True)
class _TelegramWebhookTransition:
    """What has to happen to Telegram's webhook because of an update.

    Telegram allows one webhook per bot token, so a surface that changes its
    account or its event mode has to give the old registration up before the new
    one is made. That is why both halves are decided together rather than each
    looking at the surface on its own.
    """

    register: bool
    disable: bool


def _telegram_transition(
    previous: AgentSurfaceEntity, current: AgentSurfaceEntity
) -> _TelegramWebhookTransition:
    was_enabled = previous.is_active and telegram_requires_webhook_setup(previous)
    is_enabled = current.is_active and telegram_requires_webhook_setup(current)
    binding_changed = (
        previous.account_id != current.account_id
        or previous.event_mode != current.event_mode
    )
    return _TelegramWebhookTransition(
        register=is_enabled
        and (not was_enabled or binding_changed or not current.webhook_secret),
        disable=was_enabled and (not is_enabled or binding_changed),
    )


class SurfaceTelegramWebhookMixin:
    """Split out of :class:`AgentSurfaceService`; see the module docstring."""

    async def _ensure_unique_telegram_account(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        await ensure_unique_telegram_account(
            surface, surface_repository=self.surface_repository
        )

    async def _prepare_telegram_webhook(
        self,
        surface: AgentSurfaceEntity,
    ) -> dict[str, Any]:
        """Validate the Telegram account and mint a webhook secret.

        Returns the bot credentials so the caller can register the webhook after
        the surface (and its secret) are persisted.
        """
        credentials = await self._telegram_credentials(surface)
        self._assert_public_webhook_url_or_raise()
        surface.configure_webhook_secret(secret=secrets.token_urlsafe(32))
        return credentials

    async def _telegram_credentials(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any]:
        if surface.account_id is None:
            raise AgentSurfaceValidationError(
                "Telegram WEBHOOK surfaces require account_id"
            )
        account = await self._get_connected_account(surface.account_id)
        if account.connector_id.lower() != "telegram":
            raise AgentSurfaceValidationError(
                "Telegram surfaces require a connected telegram account"
            )
        credentials = dict(account.credentials or {})
        if not str(credentials.get("bot_token") or "").strip():
            raise AgentSurfaceValidationError(
                "Telegram account credentials missing bot_token"
            )
        return credentials

    def _assert_public_webhook_url_or_raise(self) -> None:
        if not public_https_api_url_available():
            raise AgentSurfaceValidationError(
                "Telegram WEBHOOK surfaces require a public HTTPS api_url; "
                "localhost and http api_url values are not supported. Local native "
                "workers poll when ENABLE_TELEGRAM_POLLING_MODE=true."
            )

    def _build_public_surface_webhook_url(self, surface_id: UUID) -> str:
        self._assert_public_webhook_url_or_raise()
        base_url = settings.api_url.rstrip("/")
        return f"{base_url}/surfaces/{surface_id}/webhook"

    async def _register_telegram_webhook(
        self,
        *,
        credentials: dict[str, Any],
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        """Register the Telegram webhook idempotently.

        Clears any prior webhook and pending updates, sets the new webhook
        (restricted to the update types the surface handles), then verifies via
        getWebhookInfo. Each step retries on transient failures (429/5xx/network)
        honoring Telegram's retry_after. The surface row is already persisted by
        the caller; a hard failure here is surfaced as an actionable
        AgentSurfacePlatformError (with Telegram's real description) without
        rolling back the saved secret, so a transient hiccup self-heals on retry.
        """
        client = TelegramClient.from_credentials(credentials)
        try:
            await self._telegram_webhook_call(
                client, "deleteWebhook", {"drop_pending_updates": True}
            )
            await self._telegram_webhook_call(
                client,
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": webhook_secret,
                    "allowed_updates": ALLOWED_UPDATES,
                    "drop_pending_updates": True,
                },
            )
            info = await self._telegram_webhook_call(client, "getWebhookInfo", {})
        except TelegramApiError as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                "Could not configure Telegram webhook automatically. Set it "
                f"manually to {webhook_url}. Telegram response: {exc.description}",
            ) from exc
        except Exception as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                "Could not configure Telegram webhook automatically. Set it "
                f"manually to {webhook_url}.",
            ) from exc

        registered_url = str((info.get("result") or {}).get("url") or "")
        if registered_url != webhook_url:
            raise AgentSurfacePlatformError(
                "telegram",
                f"Telegram did not confirm the webhook URL (got '{registered_url}'). "
                f"Set it manually to {webhook_url}.",
            )

    async def _delete_telegram_webhook(self, surface: AgentSurfaceEntity) -> None:
        # Best-effort teardown: a Telegram outage must not block disabling or
        # deleting a surface.
        try:
            credentials = await self._telegram_credentials(surface)
            client = TelegramClient.from_credentials(credentials)
            await self._telegram_webhook_call(
                client, "deleteWebhook", {"drop_pending_updates": False}
            )
        except Exception:
            logger.debug(
                "agent_surfaces.surface_service.could_not_disable_telegram_webhook.diagnostic"
            )

    async def _telegram_webhook_call(
        self,
        client: TelegramClient,
        method: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await with_retry(
            lambda: client.call(method, payload),
            policy=_WEBHOOK_RETRY_POLICY,
            classify=classify_telegram_error,
            retry_after=telegram_retry_after,
        )
