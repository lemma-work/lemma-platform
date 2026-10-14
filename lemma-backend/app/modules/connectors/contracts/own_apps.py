"""An org's own provider app, registered by another module on the org's behalf.

The bring-your-own-app path has always ended in an ``ORG_CUSTOM`` install made
from credentials a person pasted. When Lemma creates the app itself -- a pod's
own Slack app, made with the org's configuration token -- the credentials come
back to the server, and this is how they become that same install without
passing through anybody's browser.

Goes through ``ConnectorService.create_auth_config`` rather than writing the row,
so the role check, the config validation and the first-install-is-default rule
are the ones every other install gets.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.services.connector_service import INSTALL_MANAGER_ROLES


async def may_manage_installs(uow, *, user_id: UUID, organization_id: UUID) -> bool:
    """Whether this person may add or remove the org's connector installs."""
    return await get_connector_service(
        uow
    ).organization_access.user_has_organization_role(
        user_id=user_id,
        organization_id=organization_id,
        allowed_roles=INSTALL_MANAGER_ROLES,
    )


async def create_own_app_install(
    uow,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
    name: str,
    config: dict[str, str],
) -> UUID:
    """Register the org's own app as an install, and return the install's id."""
    install = await get_connector_service(uow).create_auth_config(
        user_id=user_id,
        organization_id=organization_id,
        connector_id=connector_id,
        config_source=AuthConfigSource.ORG_CUSTOM.value,
        config=dict(config),
        name=name,
    )
    return install.id
