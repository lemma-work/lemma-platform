"""Refreshing an account's OAuth token once, with no connection held.

The refresh used to run inside the caller's unit of work, with no lock: read the
credentials, call the provider, write the answer back. Two things were wrong
with that, and both got worse the busier an account was.

*It held a pooled connection across the provider's token endpoint* -- an HTTP
round trip whose latency is somebody else's, on the path every connector
operation takes when a token has expired.

*Concurrent callers each refreshed.* An agent run fanning out five operations on
one expired account made five refresh calls with the same refresh token, and
the last writer won. Providers that rotate refresh tokens (GitHub among them)
treat reuse of a spent one as theft and revoke the grant, so the account fell to
REAUTH_REQUIRED with nobody having done anything wrong.

So the refresh is single-flight, on the vault's lease:

1. The caller read the credentials at some version.
2. It asks for the lease *at that version* and commits, so the others see it.
3. **Granted**: it calls the provider inside ``connection_released``, then writes
   the new credentials with a compare-and-set on the version it read, which also
   ends the lease. A failure gives the lease back.
4. **Refused**: someone else is refreshing (or already has). It polls the
   version -- without holding a connection between polls -- until it moves, the
   lease is given up, or it has waited as long as a lease can live, and then
   reads what is stored.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid7

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    CredentialTypes,
    OAuthCredentials,
)
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.errors import (
    AccountNotFoundError,
    ConnectorReauthRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort, AuthProviderPort
from app.modules.connectors.services.credential_refresh_failure import (
    raise_refresh_failure,
    reauth_required,
)
from app.modules.vault.contracts import LeaseToken, SecretMeta, SecretVersionConflict

logger = get_logger(__name__)

#: How long one refresher may hold the lease. Longer than any sane token
#: endpoint takes; short enough that a crashed holder does not strand the rest.
LEASE_TTL = timedelta(seconds=30)
#: How often a waiter looks at the version. A version read is one indexed
#: lookup, and no connection is held between them.
POLL_INTERVAL_SECONDS = 0.25
#: How long a waiter waits before it stops: as long as a lease can live.
WAIT_SECONDS = LEASE_TTL.total_seconds()


class _UnitOfWork(Protocol):
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    """One caller's view of an account whose token needs refreshing."""

    account: AccountEntity
    auth_provider: AuthProviderPort
    install: ResolvedAuthInstall
    #: The credentials as the caller read them, at ``account.credentials_version``.
    current: OAuthCredentials
    is_expired: bool


class CredentialRefresher:
    """Single-flight refresh of one account's credentials on ``uow``.

    Commits ``uow`` as it goes -- after taking the lease, after writing -- so
    the caller must not have uncommitted work of its own in it. The timings are
    constructor arguments so a test can shorten them rather than patch them.
    """

    def __init__(
        self,
        accounts: AccountRepositoryPort,
        uow: _UnitOfWork,
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        wait_seconds: float = WAIT_SECONDS,
        lease_ttl: timedelta = LEASE_TTL,
    ) -> None:
        self._accounts = accounts
        self._uow = uow
        self._poll_interval = poll_interval
        self._wait_seconds = wait_seconds
        self._lease_ttl = lease_ttl

    @property
    def _session(self) -> object | None:
        # The release only makes sense on a real session; a double has none,
        # and `connection_released(None)` is then just the block.
        return getattr(self._uow, "session", None)

    async def refresh(self, request: RefreshRequest) -> CredentialTypes | None:
        """Credentials good to use now: refreshed by us, or by whoever was first."""
        seen = request.account.credentials_version
        if seen is None:
            # Not read through the repository, so there is no version to lease
            # against. Refresh as before rather than refusing to.
            return await self._refresh_holding(request, None, None)
        lease = await self._accounts.try_lease_credentials(
            request.account.id,
            if_version=seen,
            holder=str(uuid7()),
            ttl=self._lease_ttl,
        )
        # Committed either way: a granted lease must be visible to the others,
        # and a refused one leaves nothing to keep a connection open for.
        await self._uow.commit()
        if lease is None:
            return await self._await_other_refresh(request, seen)
        return await self._refresh_holding(request, lease, seen)

    async def _refresh_holding(
        self, request: RefreshRequest, lease: LeaseToken | None, seen: int | None
    ) -> CredentialTypes | None:
        account = request.account
        try:
            async with connection_released(self._session):
                new_credentials = await request.auth_provider.refresh_credentials(
                    install=request.install,
                    credentials=request.current,
                    user_id=account.user_id,
                )
        except Exception as exc:
            return await self._refresh_failed(request, lease, exc)
        try:
            await self._accounts.replace_credentials(
                account.id, new_credentials, expected_version=seen, lease=lease
            )
        except SecretVersionConflict:
            # Our lease ran out mid-call and somebody else refreshed after it.
            # Theirs is stored and ours is refused, so use theirs.
            await self._uow.rollback()
            logger.warning(
                "connectors.credential_refresh.lease_lost.degraded",
                account_id=str(account.id),
                connector_id=account.connector_id,
            )
            return await self._stored_credentials(account.id)
        if account.status != AccountStatus.CONNECTED:
            # A successful refresh restores a previously-degraded account.
            await self._accounts.set_status(account.id, AccountStatus.CONNECTED)
        await self._uow.commit()
        return new_credentials

    async def _refresh_failed(
        self, request: RefreshRequest, lease: LeaseToken | None, exc: Exception
    ) -> CredentialTypes | None:
        """Give the lease back, then decide what the failure means.

        The warning below is handed ``exc`` itself, so it keeps the traceback
        without depending on being called from inside the ``except``.
        """
        account = request.account
        # An expired token we cannot refresh, or a withdrawn grant: the account
        # is unusable until the user reconnects.
        reauth = request.is_expired or isinstance(exc, ConnectorReauthRequiredError)
        if lease is not None:
            await self._accounts.release_credentials_lease(lease)
        if reauth and account.status != AccountStatus.REAUTH_REQUIRED:
            await self._accounts.set_status(account.id, AccountStatus.REAUTH_REQUIRED)
        await self._uow.commit()
        if reauth:
            raise_refresh_failure(account, exc)
        if isinstance(exc, DomainError):
            raise exc
        # The stored token has not expired yet, so the account keeps working --
        # for now. But a provider that rejects a refresh has usually revoked the
        # grant, which is exactly the case an expiry check cannot see, and at
        # debug this left no trace at all in production: the first signal was
        # the account flipping to REAUTH_REQUIRED hours later, with nothing
        # saying when it actually broke.
        logger.warning(
            "connectors.connector_service.credential_refresh_rejected.degraded",
            account_id=str(account.id),
            connector_id=account.connector_id,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return request.current

    async def _await_other_refresh(
        self, request: RefreshRequest, seen: int
    ) -> CredentialTypes | None:
        """Wait for the refresher that holds the lease, then use its result."""
        account = request.account
        deadline = monotonic() + self._wait_seconds
        while True:
            meta = await self._accounts.credentials_meta(account.id)
            if _settled(meta, seen):
                break
            if monotonic() >= deadline:
                logger.warning(
                    "connectors.credential_refresh.wait_timed_out.degraded",
                    account_id=str(account.id),
                    connector_id=account.connector_id,
                )
                break
            async with connection_released(self._session):
                await asyncio.sleep(self._poll_interval)
        return await self._after_waiting(request, seen)

    async def _after_waiting(
        self, request: RefreshRequest, seen: int
    ) -> CredentialTypes | None:
        fresh = await self._accounts.get(request.account.id)
        if fresh is None:
            raise AccountNotFoundError(str(request.account.id))
        if fresh.credentials_version != seen:
            return fresh.credentials
        # Nobody wrote. The holder failed, or took longer than its lease.
        if fresh.status == AccountStatus.REAUTH_REQUIRED:
            raise reauth_required(fresh, "refresh_failed")
        if not request.is_expired:
            return request.current
        raise OAuthWorkflowError("Unable to refresh connector credentials.")

    async def _stored_credentials(self, account_id: UUID) -> CredentialTypes | None:
        fresh = await self._accounts.get(account_id)
        if fresh is None:
            raise AccountNotFoundError(str(account_id))
        return fresh.credentials


def _settled(meta: SecretMeta | None, seen: int) -> bool:
    """Whether there is nothing left to wait for.

    The version moved (somebody wrote), the secret is gone, or nobody holds the
    lease any more -- a holder that failed gives it back without writing.
    """
    if meta is None or meta.version != seen:
        return True
    return meta.lease_until is None or meta.lease_until <= datetime.now(timezone.utc)
