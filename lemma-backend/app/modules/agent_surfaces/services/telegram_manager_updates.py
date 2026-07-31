from __future__ import annotations

import re
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    classify_telegram_error,
)
from app.modules.agent_surfaces.services.telegram_manager_runtime import (
    TelegramManagerRuntime,
)
from app.modules.agent_surfaces.services.telegram_manager_store import (
    TelegramManagedBotProvisioningClaim,
    TelegramManagedBotProvisioningInProgressError,
    TelegramManagedBotSetup,
    TelegramManagedBotSetupStatus,
)
from app.modules.connectors.domain.errors import ConnectorNotFoundError

logger = get_logger(__name__)

_START_RE = re.compile(r"^/start(?:@\w+)?\s+surface_([A-Za-z0-9_-]+)$")


class _PermanentProvisioningError(RuntimeError):
    pass


async def handle_telegram_manager_update(
    runtime: TelegramManagerRuntime,
    update: dict[str, Any],
) -> None:
    update_id = update.get("update_id")
    if isinstance(update_id, int) and await runtime._store.is_update_processed(
        update_id
    ):
        return

    message = update.get("message")
    if isinstance(message, dict):
        start_match = _START_RE.match(str(message.get("text") or "").strip())
        if start_match:
            await _handle_start(runtime, message, start_match.group(1))
        else:
            created = message.get("managed_bot_created")
            if isinstance(created, dict):
                await _handle_created(runtime, message, created)

    if isinstance(update_id, int):
        await runtime._store.mark_update_processed(update_id)


async def _handle_start(
    runtime: TelegramManagerRuntime,
    message: dict[str, Any],
    setup_id: str,
) -> None:
    setup = await runtime._store.get(setup_id)
    chat_id = _message_chat_id(message)
    telegram_user_id = _message_user_id(message)
    if setup is None:
        if chat_id is not None:
            await runtime._send_text(
                chat_id,
                "This setup link expired. Return to Lemma and start again.",
            )
        return
    if chat_id is None or telegram_user_id is None:
        return
    if await _reply_for_existing_state(runtime, setup, chat_id):
        return
    if (
        setup.telegram_user_id is not None
        and setup.telegram_user_id != telegram_user_id
    ):
        await runtime._send_text(
            chat_id,
            "This setup link is already being used in another Telegram account.",
        )
        return
    if not await runtime._store.bind_telegram_user(
        setup_id=setup.setup_id,
        telegram_user_id=telegram_user_id,
    ):
        await _reject_competing_setup(runtime, setup, chat_id)
        return
    if not await _save_waiting_setup(runtime, setup, message, telegram_user_id):
        return
    await _send_creation_keyboard(runtime, setup, chat_id)


async def _reply_for_existing_state(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    chat_id: int,
) -> bool:
    messages = {
        TelegramManagedBotSetupStatus.COMPLETE: (
            "This Telegram bot setup is already complete."
        ),
        TelegramManagedBotSetupStatus.FAILED: (
            "This setup is no longer active. Return to Lemma and try again."
        ),
        TelegramManagedBotSetupStatus.PROVISIONING: (
            "Lemma is still connecting your Telegram bot."
        ),
    }
    text = messages.get(setup.status)
    if text is None:
        return False
    if setup.status in {
        TelegramManagedBotSetupStatus.COMPLETE,
        TelegramManagedBotSetupStatus.FAILED,
    }:
        await runtime._store.release_reservations(setup)
    await runtime._send_text(chat_id, text, remove_keyboard=True)
    return True


async def _reject_competing_setup(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    chat_id: int,
) -> None:
    setup.status = TelegramManagedBotSetupStatus.FAILED
    setup.error = (
        "Another Telegram bot setup is already active for this Telegram account."
    )
    failed = await runtime._store.save_if_status(
        setup,
        expected={TelegramManagedBotSetupStatus.PENDING},
    )
    if failed:
        await runtime._store.release_reservations(setup)
    await runtime._send_text(
        chat_id,
        "Another Lemma bot setup is already active for this Telegram account. "
        "Finish it before opening this setup link.",
        remove_keyboard=True,
    )


async def _save_waiting_setup(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    message: dict[str, Any],
    telegram_user_id: int,
) -> bool:
    setup.telegram_user_id = telegram_user_id
    from_user = message.get("from") or {}
    setup.telegram_username = (
        str(from_user.get("username") or "").strip().lstrip("@") or None
    )
    setup.telegram_display_name = (
        " ".join(
            part
            for part in (
                str(from_user.get("first_name") or "").strip(),
                str(from_user.get("last_name") or "").strip(),
            )
            if part
        )
        or setup.telegram_username
    )
    setup.status = TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM
    saved = await runtime._store.save_if_status(
        setup,
        expected={
            TelegramManagedBotSetupStatus.PENDING,
            TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM,
        },
    )
    if saved:
        return True
    current = await runtime._store.get(setup.setup_id)
    if (
        current is not None
        and current.status is TelegramManagedBotSetupStatus.PROVISIONING
    ):
        chat_id = _message_chat_id(message)
        if chat_id is not None:
            await runtime._send_text(
                chat_id,
                "Lemma is still connecting your Telegram bot.",
                remove_keyboard=True,
            )
    elif current is not None and current.status in {
        TelegramManagedBotSetupStatus.COMPLETE,
        TelegramManagedBotSetupStatus.FAILED,
    }:
        await runtime._store.release_reservations(current)
    return False


async def _send_creation_keyboard(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    chat_id: int,
) -> None:
    await runtime._client.call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                "Create a dedicated Telegram bot for this Lemma surface. "
                "You can edit the suggested name and username before confirming."
            ),
            "reply_markup": {
                "keyboard": [
                    [
                        {
                            "text": "Create Telegram bot",
                            "request_managed_bot": {
                                "request_id": setup.request_id,
                                "suggested_name": setup.suggested_bot_name,
                                "suggested_username": (
                                    setup.suggested_bot_username[:-3]
                                ),
                            },
                        }
                    ]
                ],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        },
    )


async def _handle_created(
    runtime: TelegramManagerRuntime,
    message: dict[str, Any],
    created: dict[str, Any],
) -> None:
    telegram_user_id = _message_user_id(message)
    bot = created.get("bot")
    if telegram_user_id is None or not isinstance(bot, dict):
        return
    setup = await _resolve_created_setup(runtime, created, telegram_user_id)
    if setup is not None and setup.status in {
        TelegramManagedBotSetupStatus.COMPLETE,
        TelegramManagedBotSetupStatus.FAILED,
    }:
        await runtime._store.release_reservations(setup)
        return
    if setup is None or not _setup_accepts_created_bot(
        setup,
        telegram_user_id,
    ):
        return
    parsed_bot = _parse_created_bot(bot)
    if parsed_bot is None:
        return
    bot_id, bot_username = parsed_bot
    chat_id = _message_chat_id(message)
    owner = secrets.token_urlsafe(18)
    if not await _claim_created_bot(
        runtime,
        setup,
        bot_id=bot_id,
        owner=owner,
        chat_id=chat_id,
    ):
        return

    async with runtime._renew_provisioning_lease(
        setup_id=setup.setup_id,
        owner=owner,
    ):
        if not await _begin_provisioning(
            runtime,
            setup,
            bot_id=bot_id,
            bot_username=bot_username,
            owner=owner,
        ):
            return
        completed = await _provision(
            runtime,
            setup,
            bot_id=bot_id,
            bot_username=bot_username,
            chat_id=chat_id,
            owner=owner,
        )
    if completed and chat_id is not None:
        await _send_success(runtime, setup, bot_username, chat_id)


async def _resolve_created_setup(
    runtime: TelegramManagerRuntime,
    created: dict[str, Any],
    telegram_user_id: int,
) -> TelegramManagedBotSetup | None:
    request_id = created.get("request_id")
    setup = (
        await runtime._store.get_by_request_id(request_id)
        if isinstance(request_id, int)
        else None
    )
    if setup is not None:
        return setup
    return await runtime._store.get_by_telegram_user_id(telegram_user_id)


def _setup_accepts_created_bot(
    setup: TelegramManagedBotSetup,
    telegram_user_id: int,
) -> bool:
    return bool(
        setup.telegram_user_id == telegram_user_id
        and setup.status
        in {
            TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM,
            TelegramManagedBotSetupStatus.PROVISIONING,
        }
    )


def _parse_created_bot(bot: dict[str, Any]) -> tuple[int, str | None] | None:
    try:
        bot_id = int(bot["id"])
    except KeyError, TypeError, ValueError:
        return None
    username = str(bot.get("username") or "").strip().lstrip("@") or None
    return bot_id, username


async def _claim_created_bot(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    *,
    bot_id: int,
    owner: str,
    chat_id: int | None,
) -> bool:
    claim = await runtime._store.claim_provisioning(
        setup_id=setup.setup_id,
        bot_id=bot_id,
        owner=owner,
    )
    if claim is TelegramManagedBotProvisioningClaim.ACQUIRED:
        return True
    if claim is TelegramManagedBotProvisioningClaim.IN_PROGRESS:
        raise TelegramManagedBotProvisioningInProgressError(
            f"Telegram managed-bot setup '{setup.setup_id}' is provisioning"
        )
    if chat_id is not None:
        await runtime._send_text(
            chat_id,
            "This bot does not match the active Lemma setup. Finish the active "
            "setup or return to Lemma and try again.",
            remove_keyboard=True,
        )
    return False


async def _begin_provisioning(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    *,
    bot_id: int,
    bot_username: str | None,
    owner: str,
) -> bool:
    if setup.status is TelegramManagedBotSetupStatus.PROVISIONING:
        return setup.bot_id == bot_id
    setup.status = TelegramManagedBotSetupStatus.PROVISIONING
    setup.error = None
    setup.bot_id = bot_id
    setup.bot_username = bot_username
    saved = await runtime._store.save_if_status(
        setup,
        expected={TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM},
        owner=owner,
    )
    if saved:
        return True
    current = await runtime._store.get(setup.setup_id)
    if current is not None and current.status is TelegramManagedBotSetupStatus.COMPLETE:
        return False
    raise TelegramManagedBotProvisioningInProgressError(
        f"Telegram managed-bot setup '{setup.setup_id}' changed state"
    )


async def _provision(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    *,
    bot_id: int,
    bot_username: str | None,
    chat_id: int | None,
    owner: str,
) -> bool:
    try:
        token_response = await runtime._client.call(
            "getManagedBotToken",
            {"user_id": bot_id},
        )
        bot_token = str(token_response.get("result") or "").strip()
        if not bot_token:
            raise _PermanentProvisioningError(
                "Telegram did not return the managed bot token"
            )
        account_id, surface_id = await runtime._persist_managed_bot(
            setup=setup,
            bot_id=bot_id,
            bot_username=bot_username,
            bot_token=bot_token,
        )
        await runtime._configure_managed_bot(setup=setup, bot_token=bot_token)
        await _finish_complete(
            runtime,
            setup,
            account_id,
            surface_id,
            owner=owner,
        )
        return True
    except (
        DomainError,
        IntegrityError,
        KeyError,
        _PermanentProvisioningError,
        TelegramApiError,
        TypeError,
        ValueError,
    ) as exc:
        if _is_transient_setup_error(exc):
            raise
        await _finish_failed(runtime, setup, exc, chat_id, owner=owner)
        return False


async def _finish_complete(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    account_id: UUID,
    surface_id: UUID,
    *,
    owner: str,
) -> None:
    setup.status = TelegramManagedBotSetupStatus.COMPLETE
    setup.account_id = account_id
    setup.surface_id = surface_id
    setup.error = None
    saved = await runtime._store.save_if_status(
        setup,
        expected={TelegramManagedBotSetupStatus.PROVISIONING},
        owner=owner,
    )
    if not saved:
        current = await runtime._store.get(setup.setup_id)
        if (
            current is None
            or current.status is not TelegramManagedBotSetupStatus.COMPLETE
        ):
            raise RuntimeError(
                "Telegram managed-bot setup completion was not persisted"
            )
    await runtime._store.release_reservations(setup)


async def _finish_failed(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    exc: Exception,
    chat_id: int | None,
    *,
    owner: str,
) -> None:
    logger.error(
        "agent_surfaces.telegram_manager.managed_bot_provisioning_failed",
        exc_info=True,
    )
    setup.status = TelegramManagedBotSetupStatus.FAILED
    setup.error = _safe_setup_error(exc)
    failed = await runtime._store.save_if_status(
        setup,
        expected={TelegramManagedBotSetupStatus.PROVISIONING},
        owner=owner,
    )
    if not failed:
        current = await runtime._store.get(setup.setup_id)
        if (
            current is None
            or current.status is not TelegramManagedBotSetupStatus.FAILED
        ):
            raise RuntimeError(
                "Telegram managed-bot setup failure was not persisted"
            ) from exc
    await runtime._store.release_reservations(setup)
    if chat_id is not None:
        await runtime._send_text(
            chat_id,
            "The bot was created, but Lemma could not finish connecting it. "
            "Return to Lemma and try again.",
            remove_keyboard=True,
        )


async def _send_success(
    runtime: TelegramManagerRuntime,
    setup: TelegramManagedBotSetup,
    bot_username: str | None,
    chat_id: int,
) -> None:
    handle = f"@{bot_username}" if bot_username else "your new bot"
    await runtime._send_text(
        chat_id,
        f"{handle} is connected to Lemma and ready to use.",
        remove_keyboard=True,
    )
    await runtime._client.call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Continue the conversation in your new bot.",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "Open your bot",
                            "url": runtime.bot_launch_url(setup),
                        }
                    ]
                ]
            },
        },
    )


def _message_chat_id(message: dict[str, Any]) -> int | None:
    value = (message.get("chat") or {}).get("id")
    return int(value) if isinstance(value, int) else None


def _message_user_id(message: dict[str, Any]) -> int | None:
    value = (message.get("from") or {}).get("id")
    return int(value) if isinstance(value, int) else None


def _safe_setup_error(exc: Exception) -> str:
    if isinstance(exc, ConnectorNotFoundError):
        return "Telegram connector is not installed for this organization."
    return "Lemma could not finish connecting the managed bot."


def _is_transient_setup_error(exc: Exception) -> bool:
    if isinstance(exc, TelegramApiError):
        return classify_telegram_error(exc) is DeliveryClassification.TRANSIENT
    return isinstance(exc, DomainError) and (
        exc.status_code == 429 or exc.status_code >= 500
    )
