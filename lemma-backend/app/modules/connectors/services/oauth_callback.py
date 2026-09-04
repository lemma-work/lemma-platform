"""Completing an OAuth connect, without holding a connection while we wait.

Everything between "the provider redirected somebody back" and "the account
exists" happens here. It was inline in `ConnectorService`, inside the
request-scoped unit of work, which meant one pooled database connection stayed
checked out across the token exchange, up to two profile round trips, a
Telegram `getMe` and a GitHub `GET /user/installations` -- five external calls
whose latency is set by somebody else, on a path whose *rate* is set by
somebody else too (the provider decides when to redirect). `delete_auth_config`
had already been fixed the same way; this one was missed.

So the saga is explicit, and named after the rule in
`docs/development.md` -- resolve, release, call, persist:

1. **Resolve** (short unit of work): claim the `state`, load the install, the
   connector and the auth provider.
2. **Release**: commit. Spending the `state` durably at this point is not
   merely convenient -- it is what the claim was for. Leaving the claim
   uncommitted meant a domain error during the exchange rolled it back and
   handed the `state` back to whoever held it.
3. **Call**: the provider round trips, with nothing checked out.
4. **Persist** (short unit of work): write the account and close the request.

The one thing that is *not* moved out is the profile fetch's own repository
reads -- see `ConnectorService._fetch_account_profile`, which loads the
operation rows and commits before executing any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    OAuthCredentials,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    ConnectRequestNotFoundError,
    ConnectRequestStateRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.domain.ports import (
    AuthProviderPort,
    ConnectRequestRepositoryPort,
)
from app.modules.connectors.services.account_identity import (
    ProviderPayload,
    account_email_from,
    provider_account_id_from_credentials,
    provider_account_id_from_profile,
    resolve_account_identity,
)
from app.modules.connectors.services.auth.github_installation import (
    bound_external_ref,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    oldest_claimable_connect_request,
    stored_code_verifier,
    stored_provider_state,
    without_spent_secrets,
)
from app.modules.connectors.services.install_provisioning import (
    discover_operations_for_new_account,
)
from app.modules.connectors.services.install_service_seam import InstallServiceSeam

logger = get_logger(__name__)

#: What `OAuthWorkflowError` carries back to the caller: the connector module's
#: allowlisted error keys, never a provider's free text.
ErrorDetails = dict[str, object]


class OAuthCallbackSeam(InstallServiceSeam, Protocol):
    """The slice of `ConnectorService` this saga uses.

    Wider than `InstallServiceSeam` because completing a connect touches the
    connect-request and account repositories as well as the install. Declared
    rather than taking `service: Any` for the reason the install seam gives:
    otherwise the split moves lines without moving the coupling, and the type
    checker sees none of it.
    """

    connect_request_repository: ConnectRequestRepositoryPort

    def _get_auth_provider_by_name(self, provider_name: str) -> AuthProviderPort: ...

    def _provider_value(self, auth_config: AuthConfigEntity) -> str: ...

    async def _load_native_account_profile(
        self, connector: ConnectorEntity, credentials: OAuthCredentials
    ) -> ProviderPayload | None: ...

    async def _fetch_account_profile(
        self,
        connector: ConnectorEntity,
        provider: str,
        credentials: OAuthCredentials,
    ) -> ProviderPayload | None: ...

    def _exception_details(self, exc: Exception) -> ErrorDetails | None: ...


async def handle_oauth_callback(
    service: OAuthCallbackSeam,
    *,
    redirect_uri: str,
    state: str | None = None,
) -> AccountEntity:
    """Turn a provider's redirect into a connected account."""
    if not state:
        raise ConnectRequestStateRequiredError()

    # Claimed, not merely read: the status check and the write that spends it
    # used to be separated by the whole provider exchange, so two callbacks
    # with the same `state` both passed. See `claim_pending_by_state`.
    pending_request = await service.connect_request_repository.claim_pending_by_state(
        state, not_before=oldest_claimable_connect_request()
    )
    if not pending_request:
        raise ConnectRequestNotFoundError()

    user_id = pending_request.user_id
    auth_config = await service._resolve_auth_config(
        organization_id=pending_request.organization_id,
        auth_config_id=pending_request.auth_config_id,
    )
    connector = await service.get_connector(pending_request.connector_id)
    auth_install = service._resolve_auth_install(connector, auth_config)
    auth_provider = service._get_auth_provider_by_name(
        service._provider_value(auth_config)
    )
    # Everything the provider calls below need is now in hand, so hand the
    # connection back before the first of them.
    await service.uow.commit()

    credentials = await _exchange(
        service,
        auth_provider,
        auth_install=auth_install,
        pending_request=pending_request,
        redirect_uri=redirect_uri,
        user_id=user_id,
    )
    identity = await _resolve_identity(
        service,
        connector=connector,
        auth_config=auth_config,
        credentials=credentials,
        redirect_uri=redirect_uri,
    )

    account = await _persist_account(
        service,
        pending_request=pending_request,
        auth_config=auth_config,
        connector=connector,
        identity=identity,
    )

    # After the commit: an install whose token lives on the account can only be
    # discovered now, and a discovery failure must not undo the connection the
    # person just made.
    await discover_operations_for_new_account(service, auth_config)

    return account


@dataclass(frozen=True)
class _ConnectedIdentity:
    """What the provider round trips told us about the person connecting.

    Frozen and session-free on purpose: it is the whole result of the phase
    that may not hold a connection, handed to the phase that does.
    """

    credentials: OAuthCredentials
    provider_account_id: str | None
    email: str | None
    display_name: str | None
    external_ref: str | None


async def _exchange(
    service: OAuthCallbackSeam,
    auth_provider: AuthProviderPort,
    *,
    auth_install: object,
    pending_request: ConnectRequestEntity,
    redirect_uri: str,
    user_id: UUID,
) -> OAuthCredentials:
    try:
        return await auth_provider.exchange_code_for_credentials(
            install=auth_install,
            redirect_uri=redirect_uri,
            user_id=user_id,
            # What we recorded when this flow was started, not what the caller
            # put in the URL. A scheme that identifies the resulting connection
            # by a query parameter has no other way to tell whether the callback
            # belongs to the flow it is completing.
            state=stored_provider_state(pending_request),
            code_verifier=stored_code_verifier(pending_request),
        )
    except DomainError:
        raise
    except Exception as exc:
        logger.debug(
            "connectors.connector_service.exchange_connector_authorization_code.propagated",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        pending_request.status = ConnectRequestStatus.ERROR
        # The verifier and the provider's own handle on this authorization are
        # spent whether or not the exchange worked, and a failed flow is the one
        # nobody comes back to -- so this row would otherwise keep them forever.
        pending_request.attributes = without_spent_secrets(pending_request)
        await service.connect_request_repository.update(pending_request)
        await service.uow.commit()
        raise OAuthWorkflowError(
            "Unable to complete the OAuth flow.",
            details=service._exception_details(exc),
        ) from exc


async def _resolve_identity(
    service: OAuthCallbackSeam,
    *,
    connector: ConnectorEntity,
    auth_config: AuthConfigEntity,
    credentials: OAuthCredentials,
    redirect_uri: str,
) -> _ConnectedIdentity:
    """Ask the provider who just connected. No database work happens here."""
    provider_account_id = provider_account_id_from_credentials(
        connector.id, credentials
    )
    native_profile = await service._load_native_account_profile(connector, credentials)
    if native_profile:
        credentials = credentials.model_copy(
            update={
                "user_data": {
                    **(credentials.user_data or {}),
                    "profile": native_profile,
                }
            }
        )
    email_profile = await service._fetch_account_profile(
        connector,
        service._provider_value(auth_config),
        credentials,
    )
    # The one profile this app actually populates. The catalog-driven fetch
    # works for any connector with a profile operation configured; the
    # Lemma-native one covers Gmail/Drive/Slack alone, and only one of the two
    # is ever populated for a given app.
    #
    # Reading the identity from `native_profile` only meant every `http`-kind
    # connector with a profile operation -- GitHub, and every native connector
    # after it -- stored an account with no provider identity. That is the value
    # the duplicate-connect guard and re-auth matching key on, so without it a
    # second identity's re-auth is matched to the user's default account and
    # overwrites its credentials.
    account_profile = email_profile or native_profile
    provider_account_id = provider_account_id or provider_account_id_from_profile(
        connector.id, account_profile
    )
    email = account_email_from(
        connector.id, credentials, email_profile
    ) or account_email_from(connector.id, credentials, native_profile)

    # Human-friendly label for the account list (team name / mailbox / …),
    # falling back to the email when the app has no better label.
    display_name = (
        await resolve_account_identity(
            connector_id=connector.id,
            credentials=credentials,
            profile=account_profile,
        )
    ).display_name or email

    # Re-derived on every re-auth: a reconnect is how an account moves to
    # another workspace or installation, and a stale key keeps sending it
    # someone else's events. A GitHub App install names it on the callback and
    # only then, so a reconnect asks GitHub which one the token is for.
    external_ref = await bound_external_ref(connector.id, credentials, redirect_uri)
    return _ConnectedIdentity(
        credentials=credentials,
        provider_account_id=provider_account_id,
        email=email,
        display_name=display_name,
        external_ref=external_ref,
    )


async def _persist_account(
    service: OAuthCallbackSeam,
    *,
    pending_request: ConnectRequestEntity,
    auth_config: AuthConfigEntity,
    connector: ConnectorEntity,
    identity: _ConnectedIdentity,
) -> AccountEntity:
    """The second short unit of work: store the outcome and close the request."""
    user_id = pending_request.user_id
    provider_account_id = identity.provider_account_id

    # Match the re-authing identity to an existing account:
    #  - provider account id known → the account with exactly that id. A new
    #    identity has none yet, so `account` stays None and the create branch
    #    below runs — we must NOT fall back to the default here, or a second
    #    identity's re-auth would clobber the default account's credentials
    #    (which may itself have a null provider_account_id).
    #  - provider account id absent → the user's default for this auth config
    #    (a plain re-auth of the existing/only account; can't disambiguate
    #    identities without an id).
    if provider_account_id:
        account = await service.account_repository.get_by_user_auth_config_and_provider_account(
            user_id, auth_config.id, provider_account_id
        )
        # A healthy account for this identity blocks a duplicate connect; an
        # unhealthy one is updated in place below (a genuine reconnect).
        if account is not None and account.status == AccountStatus.CONNECTED:
            raise AccountAlreadyConnectedError(connector.id)
    else:
        account = await service.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )

    if account:
        account.credentials = identity.credentials
        account.external_ref = identity.external_ref
        if provider_account_id:
            account.provider_account_id = provider_account_id
        if identity.email:
            account.email = identity.email
        if identity.display_name:
            account.display_name = identity.display_name
        # A successful (re-)auth restores the account to a usable state.
        account.status = AccountStatus.CONNECTED
        account = await service.account_repository.update(account)
    else:
        # A new identity is the default only if the user has no account yet.
        has_existing = await service.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )
        account = await service.account_repository.create(
            AccountEntity(
                user_id=user_id,
                organization_id=pending_request.organization_id,
                auth_config_id=auth_config.id,
                connector_id=connector.id,
                is_default=has_existing is None,
                credentials=identity.credentials,
                provider_account_id=provider_account_id,
                external_ref=identity.external_ref,
                email=identity.email,
                display_name=identity.display_name,
            )
        )

    pending_request.status = ConnectRequestStatus.SUCCESS
    pending_request.attributes = without_spent_secrets(pending_request)
    await service.connect_request_repository.update(pending_request)
    await service.uow.commit()
    return account
