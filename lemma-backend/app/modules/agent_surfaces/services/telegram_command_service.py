from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    TelegramMiniApp,
    resolve_telegram_mini_app,
)


async def handle_telegram_command(
    *,
    context,
    adapter,
    credentials: dict[str, Any],
    uow_factory,
    conversation_service_factory,
    uow,
    conversation_service,
) -> bool:
    if context.platform is not SurfacePlatform.TELEGRAM:
        return False
    text = str(context.message_text or "").strip()
    if not text.startswith("/"):
        return False
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command not in {"/start", "/help", "/retry"}:
        return False
    mini_app = await _telegram_mini_app_for_context(
        context,
        uow_factory=uow_factory,
        uow=uow,
    )
    if command in {"/start", "/help"}:
        agent_name = (
            context.agent_display_name or context.surface_name or "your Lemma agent"
        )
        app_help = (
            f"Open {mini_app.label} from the app button beside the message field"
            if mini_app and mini_app.url
            else "A pod owner can connect a Mini App from this bot’s surface settings"
        )
        await adapter.send_message(
            credentials=credentials,
            event=context.event,
            message=(
                f"Hi — I’m **{agent_name}**.\n\n"
                "Send me a message, voice note, photo, or file. "
                "I’ll keep the work connected to this pod and show progress "
                "while I’m working.\n\n"
                f"{app_help}. Use `/retry` after a failed request."
            ),
        )
        return True
    retried = await _retry_failed_conversation(
        context,
        uow_factory=uow_factory,
        conversation_service_factory=conversation_service_factory,
        conversation_service=conversation_service,
    )
    await adapter.send_message(
        credentials=credentials,
        event=context.event,
        message=(
            "Retrying the last failed request."
            if retried
            else "There isn’t a failed request I can safely retry here."
        ),
    )
    return True


async def _telegram_mini_app_for_context(
    context,
    *,
    uow_factory,
    uow,
) -> TelegramMiniApp | None:
    if context.pod_id is None or context.surface_config is None:
        return None
    app_name = context.surface_config.telegram.app_name
    if app_name is None:
        return None
    if uow_factory is not None:
        async with uow_factory() as scoped_uow:
            return await resolve_telegram_mini_app(
                uow=scoped_uow,
                pod_id=context.pod_id,
                app_name=app_name,
            )
    if uow is None:
        return None
    return await resolve_telegram_mini_app(
        uow=uow,
        pod_id=context.pod_id,
        app_name=app_name,
    )


async def _retry_failed_conversation(
    context,
    *,
    uow_factory,
    conversation_service_factory,
    conversation_service,
) -> bool:
    if context.pod_id is None:
        return False
    pod_id = context.pod_id

    async def run(service) -> bool:
        retry_service = ConversationRetryService(
            uow=service.uow,
            conversation_repository=service.conversation_repository,
            agent_repository=service.agent_repository,
            authorization_service=service.authorization_service,
            fallback_model_name=service.fallback_model_name,
            usage_service=service.usage_service,
        )
        auth_ctx = await create_authorization_data_service(
            service.uow
        ).build_user_context(
            user_id=context.user_id,
            pod_id=pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            await retry_service.retry_failed_run(
                conversation_id=context.conversation_id,
                user_id=context.user_id,
                pod_id=pod_id,
                agent_name=context.agent_name,
            )
            return True
        except DomainError, RuntimeError, SQLAlchemyError, TypeError, ValueError:
            return False
        finally:
            reset_current_context(token)

    if uow_factory is not None:
        if conversation_service_factory is None:
            return False
        async with uow_factory() as scoped_uow:
            return await run(conversation_service_factory(scoped_uow))
    if conversation_service is None:
        return False
    return await run(conversation_service)
