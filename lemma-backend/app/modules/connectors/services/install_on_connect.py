"""Which install a connect runs through, installed now when there is none.

A new organization has no installs. The web app's Connect always installed
Lemma's own app for a connector client-side before asking for a sign-in link;
an agent or the CLI asking by connector id got "Connector 'gmail' not found"
instead, on every connector a new organization had -- the connector was there,
the organization's install of it was not, and nothing said who could add one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
    reject_if_disabled,
)
from app.modules.connectors.domain.connect_request import ConnectRequestEntity
from app.modules.connectors.domain.errors import (
    ConnectorConflictError,
    ConnectorDomainError,
    ConnectorNotFoundError,
    ConnectorNotInstalledError,
    InstallationNotFoundError,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    reusable_connect_request_cutoff,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.connector_service import ConnectorService


async def missing_install(
    service: ConnectorService,
    *,
    connector_id: str | None,
    auth_config_name: str | None,
    auth_config_id: UUID | None,
) -> ConnectorDomainError:
    """What was actually missing, said as that.

    A connector the catalog has but the organization never installed, an
    installation name nobody uses, and a connector that does not exist are
    three different remedies; all three used to read "Connector 'x' not found".
    """
    if auth_config_id is not None:
        return InstallationNotFoundError(str(auth_config_id))
    name = connector_id or auth_config_name or ""
    catalog_entry = await service.connector_repository.get(name)
    if catalog_entry is not None:
        return ConnectorNotInstalledError(
            name, title=catalog_entry.title, reason="not_installed"
        )
    if connector_id is not None:
        return ConnectorNotFoundError(connector_id)
    return InstallationNotFoundError(name)


async def install_lemma_app(
    service: ConnectorService,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
) -> AuthConfigEntity:
    """The organization's install of Lemma's own app for a connector, made if
    it has none.

    Only for a member who may install (``org.installation.manage``); anyone
    else is told the connector is not installed and who adds it. An install an
    admin switched off stays off. Install names are unique per organization,
    so two first connects at once collide on the name and the loser reads the
    winner's install.
    """
    repository = service.auth_config_repository

    async def held() -> AuthConfigEntity | None:
        return await repository.get_active_by_org_app_and_source(
            organization_id, connector_id, AuthConfigSource.SYSTEM_DEFAULT.value
        )

    if (existing := await held()) is not None:
        return existing
    switched_off = await repository.get_disabled_by_org_and_app(
        organization_id, connector_id
    )
    if switched_off is not None:
        reject_if_disabled(switched_off)
    catalog_entry = await service.connector_repository.get(connector_id)
    if catalog_entry is None:
        raise ConnectorNotFoundError(connector_id)
    if not await service.may_manage_installs(
        user_id=user_id, organization_id=organization_id
    ):
        raise ConnectorNotInstalledError(
            connector_id, title=catalog_entry.title, reason="not_installed"
        )
    name_taken = (
        await repository.get_active_by_org_and_name(organization_id, connector_id)
        is not None
    )
    try:
        return await service.create_auth_config(
            user_id=user_id,
            organization_id=organization_id,
            connector_id=connector_id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            # Taken by an install the organization brought: Lemma's beside it.
            name=f"{connector_id}-lemma" if name_taken else None,
        )
    except ConnectorConflictError:
        if (raced := await held()) is not None:
            return raced
        raise


async def install_to_connect(
    service: ConnectorService,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str | None,
    auth_config_id: UUID | None,
) -> AuthConfigEntity:
    """The install a connect runs through: the one named, else the
    connector's default, else Lemma's own app installed now."""
    if auth_config_id is None and connector_id is not None:
        found = await service.auth_config_repository.get_active_by_org_and_app(
            organization_id, connector_id
        )
        if found is not None:
            return found
        return await install_lemma_app(
            service,
            user_id=user_id,
            organization_id=organization_id,
            connector_id=connector_id,
        )
    return await service._resolve_auth_config(
        organization_id=organization_id,
        connector_id=connector_id,
        auth_config_id=auth_config_id,
    )


async def open_connect_request(
    service: ConnectorService,
    *,
    user_id: UUID,
    auth_config_id: UUID,
    return_to: str | None,
    purpose: str | None,
    reusable: bool,
) -> ConnectRequestEntity | None:
    """A sign-in this person started on this install moments ago and has not
    finished, handed back instead of a new one.

    Every click of Connect made another pending request and another
    provider-side authorization, each a round trip to the provider. The same
    person asking again for the same install, to come back to the same place,
    is asking for the same link. One that carries per-connection fields or
    drops authorize parameters is built for that call alone and never reused.
    """
    if not reusable:
        return None
    return await service.connect_request_repository.get_reusable_pending(
        user_id=user_id,
        auth_config_id=auth_config_id,
        return_to=return_to,
        purpose=purpose,
        not_before=reusable_connect_request_cutoff(),
    )


__all__ = [
    "install_lemma_app",
    "install_to_connect",
    "missing_install",
    "open_connect_request",
]
