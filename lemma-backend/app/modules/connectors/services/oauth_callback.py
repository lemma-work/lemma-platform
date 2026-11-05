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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.work import facts
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
from app.modules.connectors.contracts.facts import ConnectorAccountConnected
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.errors import (
    ConnectRequestBrowserMismatchError,
    ConnectRequestIdentityMismatchError,
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
    proved_external_ref,
)
from app.modules.connectors.services.github_callback_binding import (
    bind_or_leave_for_an_admin,
    closed_attributes,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    followup_account_id,
    followup_identity,
    oldest_claimable_connect_request,
    purpose_of,
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
    browser_user_id: UUID | None = None,
) -> AccountEntity:
    """Turn a provider's redirect into a connected account.

    ``browser_user_id`` is whose Lemma session the returning browser carried,
    if any. It must be the person the request was made for (audit H2): a link
    minted by one person and completed in somebody else's browser would put the
    second person's provider account on the first one's Lemma account. A link
    an agent or the CLI minted keeps working when its person opens it in their
    own signed-in browser, which is the only place it was ever meant to go.
    """
    if not state:
        raise ConnectRequestStateRequiredError()
    await _refuse_a_different_browser(service, state, browser_user_id)

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
    _enforce_followup_identity(pending_request, identity)

    if followup_account_id(pending_request) is not None:
        return await _record_installation(
            service, pending_request=pending_request, identity=identity
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


async def _refuse_a_different_browser(
    service: OAuthCallbackSeam, state: str, browser_user_id: UUID | None
) -> None:
    """Refuse before the ``state`` is claimed or the code exchanged.

    Before, not after: the provider's code is still unspent, so the right person
    can sign in and open the same link again rather than start over.
    """
    pending = await service.connect_request_repository.get_by_state(state)
    if pending is None:
        return  # the claim below reports it, with its own error
    if browser_user_id is None or browser_user_id != pending.user_id:
        raise ConnectRequestBrowserMismatchError(signed_in=browser_user_id is not None)


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
    #: GitHub says this person controls `external_ref` (audit H4).
    installation_administered: bool = False


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
    account_profile = await service._fetch_account_profile(
        connector,
        service._provider_value(auth_config),
        credentials,
    )
    # One source now, for every kind. There used to be a second -- a hardcoded
    # pair of vendored-client calls covering Gmail, Drive and Slack -- and
    # reading the identity from that one only meant every `http`-kind connector
    # with a profile operation stored an account with no provider identity. That
    # is the value the duplicate-connect guard and re-auth matching key on, so
    # without it a second identity's re-auth was matched to the user's default
    # account and overwrote its credentials.
    if account_profile:
        # `resolve_account_identity` reads `user_data.profile.email_address` for
        # Gmail, so this is load-bearing rather than decoration.
        credentials = credentials.model_copy(
            update={
                "user_data": {
                    **(credentials.user_data or {}),
                    "profile": account_profile,
                }
            }
        )
    provider_account_id = provider_account_id or provider_account_id_from_profile(
        connector.id, account_profile
    )
    email = account_email_from(connector.id, credentials, account_profile)

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
    external_ref, administered = await proved_external_ref(
        connector.id, credentials, redirect_uri
    )
    return _ConnectedIdentity(
        credentials=credentials,
        provider_account_id=provider_account_id,
        email=email,
        display_name=display_name,
        external_ref=external_ref,
        installation_administered=administered,
    )


def _enforce_followup_identity(
    pending_request: ConnectRequestEntity, identity: _ConnectedIdentity
) -> None:
    """Refuse a second leg that came back as a different person.

    Only follow-up requests carry an expected identity, so every ordinary
    connect passes straight through -- there is nobody to expect yet. See
    `followup_attributes` for why this stands in for PKCE on that leg.
    """
    if followup_account_id(pending_request) is None:
        # Not a follow-up: there is nobody to expect yet.
        return
    expected = followup_identity(pending_request)
    if expected is None:
        # A follow-up with no expected identity has no protection at all, since
        # this check is what stands in for the PKCE that leg cannot have. That
        # can happen when the first leg's profile lookup failed and the account
        # was stored without a `provider_account_id`. Refuse rather than wave it
        # through -- `initiate_followup_request` also declines to mint one now,
        # so this is the second of two closed doors.
        logger.warning(
            "connectors.oauth_callback.followup_identity_absent.denied",
            connector_id=pending_request.connector_id,
        )
        raise ConnectRequestIdentityMismatchError()
    if str(identity.provider_account_id or "") != expected:
        logger.warning(
            "connectors.oauth_callback.followup_identity_mismatch.denied",
            connector_id=pending_request.connector_id,
        )
        raise ConnectRequestIdentityMismatchError()


async def _record_installation(
    service: OAuthCallbackSeam,
    *,
    pending_request: ConnectRequestEntity,
    identity: _ConnectedIdentity,
) -> AccountEntity:
    """Close a second leg, which has an account already and only lacks a tenant.

    The connect saga cannot be reused here. It ends in `_persist_account`, whose
    job is to decide whether this identity is a new account, a reconnect, or a
    duplicate -- and a follow-up leg is none of those. The account was created
    by the first leg moments ago and is healthy, so that function correctly
    refuses it as `ACCOUNT_ALREADY_CONNECTED`: from where it stands, somebody is
    connecting GitHub twice.

    What is actually outstanding is one field. The identity has already been
    checked against the one this leg was minted for, so the only work left is to
    record the installation that the round trip went to fetch.

    The freshly minted credentials replace the stored ones deliberately.
    Authorizing again issues a new token and may retire the previous one, so the
    newer of the two is the one worth keeping.
    """
    account_id = followup_account_id(pending_request)
    account = await service.account_repository.get(UUID(str(account_id)))
    if account is not None and account.user_id != pending_request.user_id:
        # The request names an account; it does not get to name somebody else's.
        logger.warning(
            "connectors.oauth_callback.followup_account_foreign.denied",
            connector_id=pending_request.connector_id,
        )
        raise ConnectRequestIdentityMismatchError()
    if account is None:
        # The account was deleted while its install leg was in flight. Nothing
        # to record it on, and inventing one here would sidestep every check
        # `_persist_account` exists to make.
        raise ConnectRequestNotFoundError()

    # Audit H4: the installation this leg came back with is the account's
    # organization's, or nobody's yet -- never another one's.
    identity, needs_admin = await bind_or_leave_for_an_admin(
        service.uow.session,
        identity,
        connector_id=account.connector_id,
        organization_id=account.organization_id,
        user_id=account.user_id,
    )
    account.credentials = identity.credentials
    if identity.external_ref:
        account.external_ref = identity.external_ref
    account.status = AccountStatus.CONNECTED
    account = await service.account_repository.update(account)

    pending_request.status = ConnectRequestStatus.SUCCESS
    pending_request.attributes = closed_attributes(
        pending_request, needs_admin=needs_admin
    )
    await service.connect_request_repository.update(pending_request)
    await service.uow.commit()
    logger.info(
        "connectors.oauth_callback.installation_recorded.diagnostic",
        connector_id=account.connector_id,
    )
    return account


def _renewed_credentials(
    account: AccountEntity,
    pending_request: ConnectRequestEntity,
    fresh: OAuthCredentials,
) -> OAuthCredentials:
    """What an account holds after a grant came back for it.

    A reconnect replaces the grant. A renewal another module started for a
    purpose, over an account still connected, is not the person asking to
    reconnect: the fresh grant wins, and what only the earlier one carried is
    kept. Lemma's Slack app added to a workspace asks for the bot alone, while
    the person's own Slack connect also holds their user token
    (``authed_user``), which a bot-only grant does not return and must not
    erase.
    """
    earlier = account.credentials
    if (
        account.status != AccountStatus.CONNECTED
        or purpose_of(pending_request) is None
        or not isinstance(earlier, OAuthCredentials)
        or not earlier.raw_response
    ):
        return fresh
    raw = _kept_under(earlier.raw_response, fresh.raw_response or {})
    return fresh.model_copy(update={"raw_response": raw})


def _kept_under(
    earlier: Mapping[str, object], fresh: Mapping[str, object]
) -> dict[str, object]:
    """``fresh`` over ``earlier``, key by key into nested objects."""
    merged = dict(earlier)
    for key, value in fresh.items():
        prior = merged.get(key)
        if isinstance(prior, Mapping) and isinstance(value, Mapping):
            merged[key] = _kept_under(prior, value)
        else:
            merged[key] = value
    return merged


async def _persist_account(
    service: OAuthCallbackSeam,
    *,
    pending_request: ConnectRequestEntity,
    auth_config: AuthConfigEntity,
    connector: ConnectorEntity,
    identity: _ConnectedIdentity,
) -> AccountEntity:
    """The second short unit of work: store the outcome and close the request.

    Announces ``ConnectorAccountConnected`` in the same transaction, for a new
    account and for one granted again alike.
    """
    user_id = pending_request.user_id
    provider_account_id = identity.provider_account_id
    # Audit H4. The organization and person are the connect request's -- the
    # state this callback came back under -- so an installation another
    # organization holds is refused here, before any account carries it.
    identity, needs_admin = await bind_or_leave_for_an_admin(
        service.uow.session,
        identity,
        connector_id=connector.id,
        organization_id=pending_request.organization_id,
        user_id=user_id,
    )

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
        # unhealthy one is updated in place below (a genuine reconnect). A
        # request another module started for a purpose is renewed in place
        # either way: its starter asked for a fresh grant -- Lemma's Slack app
        # added to a workspace by someone who already connected Slack -- and
        # "already connected" would end the flow it started.
        if (
            account is not None
            and account.status == AccountStatus.CONNECTED
            and purpose_of(pending_request) is None
        ):
            raise AccountAlreadyConnectedError(connector.id)
    else:
        account = await service.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )

    if account:
        account.credentials = _renewed_credentials(
            account, pending_request, identity.credentials
        )
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
        renewed = True
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
        renewed = False

    pending_request.status = ConnectRequestStatus.SUCCESS
    pending_request.attributes = closed_attributes(
        pending_request, needs_admin=needs_admin
    )
    await service.connect_request_repository.update(pending_request)
    await facts.emit(
        service.uow,
        ConnectorAccountConnected(
            account_id=account.id,
            auth_config_id=auth_config.id,
            connector_id=connector.id,
            organization_id=pending_request.organization_id,
            user_id=user_id,
            purpose=purpose_of(pending_request),
            renewed=renewed,
        ),
    )
    await service.uow.commit()
    return account
