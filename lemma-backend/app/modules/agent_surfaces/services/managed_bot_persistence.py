from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    surface_connector_binding,
)
from app.modules.agent_surfaces.services.managed_bot_identity import (
    link_managed_bot_creator,
)
from app.modules.agent_surfaces.services.telegram_manager_store import (
    TelegramManagedBotSetup,
)
from app.modules.connectors.contracts.surfaces import upsert_bot_token_account


async def persist_managed_bot(
    *,
    uow_factory: UnitOfWorkFactory,
    setup: TelegramManagedBotSetup,
    bot_id: int,
    bot_username: str | None,
    bot_token: str,
) -> tuple[UUID, UUID]:
    async with uow_factory() as uow:
        display_name = f"@{bot_username}" if bot_username else str(bot_id)
        telegram = surface_connector_binding(SurfacePlatform.TELEGRAM)
        account_id = await upsert_bot_token_account(
            uow,
            connector_id=telegram.connector_id,
            connector_kind=telegram.kind,
            organization_id=setup.organization_id,
            user_id=setup.user_id,
            provider_account_id=str(bot_id),
            display_name=display_name,
            bot_token=bot_token,
        )
        from app.modules.agent_surfaces.composition import build_surface_service

        surface_service = build_surface_service(uow)
        surface = await surface_service.surface_repository.get_by_pod_and_name(
            pod_id=setup.pod_id,
            name=setup.surface_name,
        )
        if surface is None:
            # An agent reaches Telegram in one place, so the setup target is
            # really "this agent's Telegram surface" and the name is only how a
            # caller asks for it. Keyed on the name alone, a rerun under a
            # different name found nothing, and the create below then hit
            # `uq_agent_surface_agent_type` -- an IntegrityError where the branch
            # underneath already had the right thing to say.
            same_agent, _ = await surface_service.surface_repository.list_by_pod(
                setup.pod_id,
                platform=SurfacePlatform.TELEGRAM.value,
                agent_id=setup.agent_id,
                match_agent=True,
            )
            surface = same_agent[0] if same_agent else None
        if surface is None:
            surface = await surface_service.create_surface(
                pod_id=setup.pod_id,
                agent_id=setup.agent_id,
                platform=SurfacePlatform.TELEGRAM,
                name=setup.surface_name,
                config=SurfaceConfig.model_validate(setup.surface_config),
                credential_mode=SurfaceCredentialMode.CUSTOM,
                account_id=account_id,
            )
        elif (
            surface.surface_type is not SurfacePlatform.TELEGRAM
            or surface.credential_mode is not SurfaceCredentialMode.CUSTOM
            or surface.account_id != account_id
        ):
            raise DomainError(
                "A different surface already owns this Telegram setup target",
                code="TELEGRAM_MANAGED_BOT_SURFACE_CONFLICT",
                status_code=409,
            )
        if surface.is_active != setup.is_enabled:
            surface = await surface_service.update_surface(
                surface_id=surface.id,
                is_active=setup.is_enabled,
            )
        await link_managed_bot_creator(
            uow=uow,
            telegram_user_id=setup.telegram_user_id,
            telegram_username=setup.telegram_username,
            telegram_display_name=setup.telegram_display_name,
            setup_id=setup.setup_id,
            user_id=setup.user_id,
        )
        await uow.commit()
        return account_id, surface.id
