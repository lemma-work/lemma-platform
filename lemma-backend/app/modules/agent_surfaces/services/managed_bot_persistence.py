from __future__ import annotations

from typing import Any

from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.agent_surfaces.domain.entities import (
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.managed_bot_identity import (
    link_managed_bot_creator,
)
from app.modules.connectors.domain.account import AccountEntity, GenericCredentials
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
)
from app.modules.connectors.contracts import AuthProvider
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)


async def persist_managed_bot(
    *,
    uow_factory,
    setup: Any,
    bot_id: int,
    bot_username: str | None,
    bot_token: str,
):
    async with uow_factory() as uow:
        auth_configs = AuthConfigRepository(
            uow=uow,
            encryption=get_secret_cipher(),
            message_bus=get_message_bus(),
        )
        auth_config = await auth_configs.get_active_by_org_and_app(
            setup.organization_id,
            "telegram",
        )
        if auth_config is None:
            auth_config = await auth_configs.create(
                AuthConfigEntity(
                    organization_id=setup.organization_id,
                    connector_id="telegram",
                    provider=AuthProvider.LEMMA,
                    config_source=AuthConfigSource.SYSTEM_DEFAULT,
                    name="telegram",
                    created_by_user_id=setup.user_id,
                    updated_by_user_id=setup.user_id,
                )
            )
        accounts = AccountRepository(
            uow=uow,
            encryption=get_secret_cipher(),
            message_bus=get_message_bus(),
        )
        account = await accounts.get_by_user_auth_config_and_provider_account(
            setup.user_id,
            auth_config.id,
            str(bot_id),
        )
        if account is None:
            default_account = await accounts.get_by_user_and_auth_config(
                setup.user_id,
                auth_config.id,
            )
            account = await accounts.create(
                AccountEntity(
                    user_id=setup.user_id,
                    organization_id=setup.organization_id,
                    auth_config_id=auth_config.id,
                    connector_id="telegram",
                    is_default=default_account is None,
                    email=None,
                    credentials=GenericCredentials.model_validate(
                        {"bot_token": bot_token}
                    ),
                    provider_account_id=str(bot_id),
                    display_name=f"@{bot_username}" if bot_username else str(bot_id),
                    preferences=None,
                    allowed_scopes=None,
                    connector=None,
                )
            )
        else:
            account.credentials = GenericCredentials.model_validate(
                {"bot_token": bot_token}
            )
            account.display_name = f"@{bot_username}" if bot_username else str(bot_id)
            account = await accounts.update(account)
        from app.modules.agent_surfaces.api.dependencies import get_surface_service

        surface_service = get_surface_service(uow)
        surface = await surface_service.surface_repository.get_by_pod_and_name(
            pod_id=setup.pod_id,
            name=setup.surface_name,
        )
        if surface is None:
            surface = await surface_service.create_surface(
                pod_id=setup.pod_id,
                agent_id=setup.agent_id,
                platform=SurfacePlatform.TELEGRAM,
                name=setup.surface_name,
                config=SurfaceConfig.model_validate(setup.surface_config),
                credential_mode=SurfaceCredentialMode.CUSTOM,
                account_id=account.id,
            )
        elif (
            surface.surface_type is not SurfacePlatform.TELEGRAM
            or surface.credential_mode is not SurfaceCredentialMode.CUSTOM
            or surface.account_id != account.id
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
        return account.id, surface.id
