"""Registering and revoking a Telegram bot's webhook.

Telegram allows exactly one webhook per bot token, so a surface changing its
account or event mode has to give the old registration up before the new one is
made -- and the two halves of that decision are made together, in
:func:`_telegram_transition`, rather than each looking at the surface alone.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import (
    SurfaceCredentials,
    TelegramCredentials,
)

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID


from app.core.config import reveal_secret, settings
from app.core.infrastructure.db.session_uow import commit_now
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent_surfaces.platforms.common import (
    platform_transport_errors,
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
    AgentSurfaceStatus,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfacePlatformError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.services.surface_problems import (
    clear_problem,
    note_problem,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy

logger = get_logger(__name__)

# Bounded retry for the in-process Telegram webhook registration calls.
_WEBHOOK_RETRY_POLICY = RetryPolicy(max_attempts=3, base_delay=0.5)


@dataclass(frozen=True, slots=True)
class _TelegramWebhookTransition:
    """What has to happen to Telegram's webhook because of an update.

    Telegram allows one webhook per bot token, so a surface that changes its
    account has to give the old registration up before the new one is made.
    That is why both halves are decided together rather than each looking at the
    surface on its own.
    """

    register: bool
    disable: bool
    #: Another bot now: its queue holds nothing of this surface's.
    binding_changed: bool = False


def _telegram_transition(
    previous: AgentSurfaceEntity, current: AgentSurfaceEntity
) -> _TelegramWebhookTransition:
    was_enabled = previous.is_active and telegram_requires_webhook_setup(previous)
    is_enabled = current.is_active and telegram_requires_webhook_setup(current)
    # The connector, and only the connector. This also compared `event_mode`,
    # which had one member -- so the second half of the `or` was always False.
    binding_changed = previous.pod_connector_slug != current.pod_connector_slug
    # A registration that never completed is retried by the next save, which
    # is how the person who was told it failed puts it right.
    never_registered = previous.status is AgentSurfaceStatus.ERROR
    return _TelegramWebhookTransition(
        register=is_enabled
        and (
            not was_enabled
            or binding_changed
            or not reveal_secret(current.webhook_secret)
            or never_registered
        ),
        disable=was_enabled and (not is_enabled or binding_changed),
        binding_changed=binding_changed,
    )


if TYPE_CHECKING:
    from app.modules.agent_surfaces.domain.ports import (
        SurfaceAccountInfo,
        SurfaceAccountPort,
        SurfaceInstallationRepositoryPort,
    )


class SurfaceTelegramWebhookMixin:
    """Split out of :class:`AgentSurfaceService`; see the module docstring.

    The two collaborators below are declared, not assigned, as on
    `SurfaceConsentMixin`: they belong to `AgentSurfaceService`, which composes
    this, and declaring them is what lets a type checker see through it.
    """

    surface_repository: SurfaceInstallationRepositoryPort
    _get_connected_account: Callable[[UUID], Awaitable[SurfaceAccountInfo]]
    _accounts: Callable[[], SurfaceAccountPort]

    async def _prepare_telegram_webhook(
        self,
        surface: AgentSurfaceEntity,
    ) -> TelegramCredentials:
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
    ) -> TelegramCredentials:
        account_id = await self._accounts().bound_account_id(surface)
        if account_id is None:
            raise AgentSurfaceValidationError(
                "Telegram WEBHOOK surfaces run as a pod connector"
            )
        account = await self._get_connected_account(account_id)
        if account.connector_id.lower() != "telegram":
            raise AgentSurfaceValidationError(
                "Telegram surfaces require a connected telegram account"
            )
        credentials = TelegramCredentials.from_payload(account.credentials or {})
        if credentials.bot_token is None:
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

    async def _register_committed_telegram_webhook(
        self,
        surface: AgentSurfaceEntity,
        *,
        credentials: SurfaceCredentials,
        drop_pending_updates: bool,
    ) -> AgentSurfaceEntity:
        """Register the webhook for a surface whose row has been committed.

        Committed first, for both of the things that went wrong when this ran
        inside the request transaction. A failure raised here rolled the row
        back -- so a ``setWebhook`` that had already succeeded left Telegram
        posting, with the new secret, to a surface that did not exist (or still
        had the old secret). And the transaction held a pooled connection across
        every call to Telegram and their retries.

        The outcome is recorded on the row: a registration Telegram refused
        marks the surface ERROR, which the next save retries; one that
        completes clears it.
        """
        await commit_now(self.surface_repository)
        refused: AgentSurfacePlatformError | None = None
        try:
            await self._register_telegram_webhook(
                credentials=credentials,
                webhook_url=self._build_public_surface_webhook_url(surface.id),
                webhook_secret=reveal_secret(surface.webhook_secret) or "",
                drop_pending_updates=drop_pending_updates,
            )
        except AgentSurfacePlatformError as exc:
            refused = exc
        # Why it does not work, for its readers (``surface_problems``).
        async with connection_released(
            getattr(self.surface_repository.uow, "session", None)
        ):
            if refused is None:
                await clear_problem(surface.id)
            else:
                await note_problem(surface.id, refused.message)
        if refused is not None:
            surface.mark_webhook_unregistered()
            await self.surface_repository.update(surface)
            await commit_now(self.surface_repository)
            raise refused
        if surface.status is AgentSurfaceStatus.ERROR:
            surface.activate()
            return await self.surface_repository.update(surface)
        return surface

    async def _register_telegram_webhook(
        self,
        *,
        credentials: SurfaceCredentials,
        webhook_url: str,
        webhook_secret: str,
        drop_pending_updates: bool = True,
    ) -> None:
        """Register the Telegram webhook idempotently.

        Clears any prior webhook, sets the new webhook (restricted to the update
        types the surface handles), then verifies via getWebhookInfo. Each step
        retries on transient failures (429/5xx/network) honoring Telegram's
        retry_after. A hard failure is an actionable AgentSurfacePlatformError
        with Telegram's real description.

        ``drop_pending_updates`` is for a bot new to this surface, whose queue
        holds nothing of ours. On re-registration the queue is the person's
        messages, waiting to be delivered, and dropping it lost them.

        An answer from ``getWebhookInfo`` that does not match is logged, not
        raised: ``setWebhook`` was accepted, so Telegram is posting to this
        surface with this secret, and refusing would leave a surface whose
        deliveries are real recorded as one that has none.
        """
        client = TelegramClient.from_credentials(credentials)
        try:
            await self._telegram_webhook_call(
                client, "deleteWebhook", {"drop_pending_updates": drop_pending_updates}
            )
            await self._telegram_webhook_call(
                client,
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": webhook_secret,
                    "allowed_updates": ALLOWED_UPDATES,
                    "drop_pending_updates": drop_pending_updates,
                },
            )
        # No webhook URL to "set manually": a new surface Telegram refuses is
        # deleted (the next try gets a new address), and a webhook set by hand
        # lacks the secret Lemma checks deliveries against.
        except TelegramApiError as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                f"Telegram refused to set up this bot: {exc.description}. Check "
                "the bot token (from @BotFather) and try again.",
            ) from exc
        except platform_transport_errors() as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                "Could not reach Telegram to set up this bot. Try again in a moment.",
            ) from exc
        await self._confirm_telegram_webhook(client, webhook_url)

    async def _confirm_telegram_webhook(
        self, client: TelegramClient, webhook_url: str
    ) -> None:
        try:
            info = await self._telegram_webhook_call(client, "getWebhookInfo", {})
        except platform_transport_errors():
            logger.warning(
                "agent_surfaces.surface_service.telegram_webhook_unconfirmed.degraded",
                exc_info=True,
            )
            return
        registered_url = str((info.get("result") or {}).get("url") or "")
        if registered_url != webhook_url:
            logger.warning(
                "agent_surfaces.surface_service.telegram_webhook_mismatch.degraded",
                registered_url=registered_url,
                expected_url=webhook_url,
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
