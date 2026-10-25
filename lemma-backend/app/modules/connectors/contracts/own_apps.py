"""An org's own provider app, registered by another module on the org's behalf.

The bring-your-own-app path has always ended in an ``ORG_CUSTOM`` install made
from credentials a person pasted. When Lemma creates the app itself -- a pod's
own Slack app, made with the org's configuration token -- the credentials come
back to the server, and this is how they become that same install without
passing through anybody's browser.

Goes through ``ConnectorService.create_auth_config`` rather than writing the row,
so the role check and the config validation are the ones every other install
gets. Unlike a hand-made install it is never made the org's default: it is one
teammate's app, and nobody chose it to answer a bare connector lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import SecretStr

from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.domain.auth_config import (
    APP_MADE_BY_LEMMA,
    AuthConfigSource,
)
from app.modules.connectors.domain.errors import (
    ConnectorConflictError,
    ConnectorNotFoundError,
)


async def may_manage_installs(uow, *, user_id: UUID, organization_id: UUID) -> bool:
    """Whether this person may add or remove the org's connector installs."""
    return await get_connector_service(uow).may_manage_installs(
        user_id=user_id, organization_id=organization_id
    )


async def create_own_app_install(
    uow,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
    name: str,
    config: dict[str, str | SecretStr],
    made_by_lemma: bool = False,
) -> UUID:
    """Register the org's own app as an install, and return the install's id.

    Commits: ``create_auth_config`` commits before validating and again after
    inserting, so a caller that fails afterwards undoes it with
    ``delete_unauthorised_install``, not a rollback.

    ``made_by_lemma``: Lemma created the app itself and the provider handed
    its credentials to Lemma, so no person chose its signing secret. Recorded
    on the install (``APP_MADE_BY_LEMMA``), the one thing every surface on the
    app reaches through its pod connector -- never on a surface, which a
    re-pointed connector would leave trusting a different app.
    """
    install = await get_connector_service(uow).create_auth_config(
        user_id=user_id,
        organization_id=organization_id,
        connector_id=connector_id,
        config_source=AuthConfigSource.ORG_CUSTOM.value,
        # Revealed here, the one place the plaintext is needed: the install's
        # config is what the connectors module encrypts and stores.
        config={
            key: value.get_secret_value() if isinstance(value, SecretStr) else value
            for key, value in config.items()
        },
        name=name,
        is_default=False,
    )
    if made_by_lemma:
        await mark_install_made_by_lemma(uow, install.id)
        await uow.commit()
    return install.id


async def mark_install_made_by_lemma(uow, auth_config_id: UUID) -> None:
    """Record on an install that Lemma made the app behind it. Not committed.

    Only for an app whose credentials the provider handed to Lemma (a pending
    Slack teammate app that carries the app id Lemma created). Idempotent.
    """
    repository = get_connector_service(uow).auth_config_repository
    install = await repository.get(auth_config_id)
    if install is None or (install.metadata or {}).get(APP_MADE_BY_LEMMA) is True:
        return
    install.metadata = {**(install.metadata or {}), APP_MADE_BY_LEMMA: True}
    await repository.update(install)


async def install_is_authorised(uow, auth_config_id: UUID) -> bool:
    """Whether anybody has connected an account through this install yet."""
    accounts = await get_connector_service(uow).account_repository.list_by_auth_config(
        auth_config_id
    )
    return bool(accounts)


async def delete_unauthorised_install(
    uow,
    *,
    organization_id: UUID,
    auth_config_id: UUID | None = None,
    name: str | None = None,
) -> bool:
    """Remove an install nobody authorised, which there is nothing to revoke
    for. ``False`` when it is gone, belongs elsewhere, or has an account --
    an install somebody connected is never removed behind their back.

    By name when the id never reached the caller: ``create_own_app_install``
    commits before it returns, so a create interrupted there leaves an install
    its caller knows only by the name it asked for.
    """
    service = get_connector_service(uow)
    repository = service.auth_config_repository
    if auth_config_id is not None:
        install = await repository.get(auth_config_id)
    elif name is not None:
        install = await repository.get_active_by_org_and_name(organization_id, name)
    else:
        return False
    if install is None or install.organization_id != organization_id:
        return False
    if await service.account_repository.list_by_auth_config(install.id):
        return False
    return await repository.delete(install.id)


async def start_authorization(
    uow,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
    auth_config_id: UUID,
    return_to: str | None = None,
    purpose: str | None = None,
    omit_authorize_params: frozenset[str] = frozenset(),
) -> str:
    """Begin authorising an account against this install; the consent page's URL.

    For a flow that starts somewhere other than Lemma's web app -- a button in
    Slack -- and so has no browser of ours to call the connect-request route.
    The callback that ends it is the ordinary one. ``purpose`` is carried to
    the ``ConnectorAccountConnected`` the grant emits, so a subscriber acts
    only on the grants it started; a grant with a purpose also renews an
    account its person already has connected, rather than refusing it as a
    duplicate. ``omit_authorize_params`` asks the consent page for less than
    the catalog does (the provider's own parameter names).
    """
    request = await get_connector_service(uow).initiate_connect_request(
        user_id=user_id,
        organization_id=organization_id,
        connector_id=connector_id,
        auth_config_id=auth_config_id,
        return_to=return_to,
        purpose=purpose,
        omit_authorize_params=omit_authorize_params,
    )
    return str(request.authorization_url or "")


async def ensure_system_install(
    uow, *, user_id: UUID, organization_id: UUID, connector_id: str
) -> UUID:
    """The organization's install of Lemma's own app for this connector, made
    if it has none -- the same install the web app's Connect makes on first
    use, and with the same name (the connector id) when that name is free.

    Only ``SYSTEM_DEFAULT``: an install the organization brought is its own
    app, never Lemma's. Made through ``create_auth_config``, so the role check
    and validation are every install's, and -- as from the web -- the first
    install of a connector in an organization becomes its default. Commits.

    Install names are unique per organization, so two of these at once (or
    the web's Connect beside one) both miss and one create is refused; that
    one reads the install the other made instead of failing.
    """
    service = get_connector_service(uow)
    repository = service.auth_config_repository

    async def held() -> UUID | None:
        found = await repository.get_active_by_org_app_and_source(
            organization_id, connector_id, AuthConfigSource.SYSTEM_DEFAULT.value
        )
        return found.id if found is not None else None

    if (existing := await held()) is not None:
        return existing
    name_taken = (
        await repository.get_active_by_org_and_name(organization_id, connector_id)
        is not None
    )
    try:
        made = await service.create_auth_config(
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
    return made.id


@dataclass(frozen=True, slots=True)
class LemmaAppClient:
    """Lemma's own OAuth app for a connector, as the deployment configured it."""

    client_id: str
    client_secret: SecretStr


async def lemma_app_client(uow, connector_id: str) -> LemmaAppClient | None:
    """The client id and secret of Lemma's own app for this connector, if the
    deployment has one -- for a flow on that app the connect requests do not
    cover, such as signing a person in with it."""
    service = get_connector_service(uow)
    try:
        connector = await service.get_connector(connector_id)
    except ConnectorNotFoundError:
        return None  # Not in this deployment's catalog at all: no app either.
    config = service.system_oauth_config.get_default_oauth_config(connector)
    if config is None or not config.client_id or not config.client_secret:
        return None
    secret = config.client_secret
    return LemmaAppClient(
        client_id=config.client_id,
        client_secret=secret if isinstance(secret, SecretStr) else SecretStr(secret),
    )
