"""Asking GitHub what an account can actually reach, whenever we need to know.

The connect redirect is not a reliable place to learn this, and cannot be made
into one. GitHub sends nothing back when an organization owner approves a
pending request hours later, and nothing at all when somebody edits an
installation's repository list -- the App manifest sets `setup_on_update: false`
and, because `request_oauth_on_install` is on, the Setup URL field is disabled
outright, so there is no URL for an update to fire at even if it were flipped.
There is also no API to change repository access with: the endpoints that add or
remove a repository from an installation take a classic personal access token
and nothing else.

So day-two correctness cannot rest on redirects. It does not need webhooks
either: the account holds a user token with a refresh token good for six months,
and `GET /user/installations` is authoritative whenever we ask it. Webhooks stay
worth having as an accelerant -- they make the common cases instant -- but a
dropped delivery then costs freshness, never correctness.

Asking is not free (it spends the *user's* shared rate limit), so this asks only
when there is something to learn: an account already bound to an installation is
ready, and answers with no call at all.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.connectors.services.auth.github_installation import (
    InstallationChoice,
    InstallationOutcome,
    InstallState,
    resolve_outcome,
)
from app.modules.connectors.services.credential_freshness import fresh_credentials

logger = get_logger(__name__)

GITHUB = "github"

#: Short. This exists to stop a page that renders several unbound accounts, or a
#: person clicking about, from spending one GitHub call each -- not to remember
#: an answer. Anything that actually changed the answer busts the key.
RECONCILE_TTL_SECONDS = 120

_CACHE_PREFIX = "connectors:github:installations"


def _is_github(account: Any) -> bool:
    return (getattr(account, "connector_id", "") or "").strip().lower() == GITHUB


class GithubInstallationReconciler:
    """What an account's installation is, resolved and recorded.

    Holds no state of its own beyond a cache handle: it is built per request
    from whatever `ConnectorService` the caller already has, so it inherits that
    caller's unit of work and authorization context rather than opening its own.
    """

    def __init__(self, connector_service: Any, cache: RedisJsonCache | None = None):
        self._service = connector_service
        # Named once so every non-database await below can hand the pooled
        # connection back. Redis is fast, but "fast" is not the rule: a session
        # holds a Postgres connection until it closes, and every await inside
        # one that is not a query shrinks the pool for everybody else.
        self._session = getattr(
            getattr(connector_service, "uow", None), "session", None
        )
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_CACHE_PREFIX,
            ttl_seconds=RECONCILE_TTL_SECONDS,
        )

    async def outcome(
        self, account: Any, *, force: bool = False
    ) -> InstallationOutcome:
        """Where this account stands, asking GitHub only when that can change it.

        A bound account is ready and costs nothing: `external_ref` is the
        installation, and an installation that has gone away announces itself
        through the `installation` webhook, which retires the account. `force`
        is for the two moments where the stored answer is the thing in doubt --
        somebody pressing Refresh, and a repository lookup that came back empty.
        """
        if not _is_github(account):
            return InstallationOutcome(InstallState.READY)
        if account.external_ref and not force:
            return InstallationOutcome(
                InstallState.READY, installation_id=str(account.external_ref)
            )

        key = str(account.id)
        if not force:
            cached = await self._cached(key)
            if cached is not None:
                return cached

        outcome = await self._ask(account)
        await self._remember(key, outcome)
        await self._bind(account, outcome)
        return outcome

    async def invalidate(self, account_id: object) -> None:
        """Forget what we last heard, because something changed it.

        Called by the webhook path: a delivery does not carry the answer in a
        form this trusts, but it does say reliably that the answer moved.
        """
        await self._cache_op(self._cache.delete(str(account_id)))

    async def _ask(self, account: Any) -> InstallationOutcome:
        credentials = await fresh_credentials(
            account, account.user_id, connector_service=self._service
        )
        token = credentials.get("access_token") if credentials else None
        reveal = getattr(token, "get_secret_value", None)
        if callable(reveal):
            token = reveal()
        if not token:
            # Nothing to ask with. Not "no installation": saying so would send
            # somebody to install an App they may already have.
            logger.warning(
                "connectors.github_reconciler.no_token.degraded",
                account_id=str(account.id),
            )
            return InstallationOutcome(InstallState.INSTALL_REQUIRED)
        # GitHub can take seconds to answer and this runs inside a request that
        # holds a pooled connection. The binding write below needs the session
        # again, so only the round trip is outside it.
        async with connection_released(self._session):
            return await resolve_outcome(str(token))

    async def _bind(self, account: Any, outcome: InstallationOutcome) -> None:
        """Record a newly resolved installation on the account.

        This is what closes the loop with no redirect and no webhook: somebody
        installs the App, comes back, and the next read of their account binds
        it. `external_ref` is the inbound routing key, so it is only ever
        written from an answer GitHub gave about this account's own token.
        """
        if not outcome.is_ready or not outcome.installation_id:
            return
        if str(account.external_ref or "") == outcome.installation_id:
            return
        account.external_ref = outcome.installation_id
        await self._service.account_repository.update(account)
        await self._service.uow.commit()
        logger.info(
            "connectors.github_reconciler.installation_bound.diagnostic",
            account_id=str(account.id),
        )

    async def _cache_op(self, awaitable: Any) -> Any:
        """Run one cache call, and never let it be the reason something failed.

        One boundary rather than three identical ones. Every caller here is
        best effort in the same way: a read that fails means asking GitHub
        again, a write that fails means asking sooner than we would have, and
        an invalidation that fails costs one short TTL of staleness. Meanwhile
        `bind_account_installation` commits before it invalidates, so an
        escaping Redis error would report failure for work that succeeded.

        The connection goes back for the duration: Redis is fast, but a session
        holds a pooled Postgres connection until it closes.
        """
        try:
            async with connection_released(self._session):
                return await awaitable
        except Exception:
            logger.warning(
                "connectors.github_reconciler.cache_unavailable.degraded", exc_info=True
            )
            return None

    async def _cached(self, key: str) -> InstallationOutcome | None:
        return _from_payload(await self._cache_op(self._cache.get_json(key)))

    async def _remember(self, key: str, outcome: InstallationOutcome) -> None:
        await self._cache_op(self._cache.set_json(key, _to_payload(outcome)))


def _to_payload(outcome: InstallationOutcome) -> dict[str, Any]:
    return {
        "state": outcome.state.value,
        "installation_id": outcome.installation_id,
        "choices": [
            {
                "installation_id": choice.installation_id,
                "account_login": choice.account_login,
                "account_type": choice.account_type,
                "repository_selection": choice.repository_selection,
            }
            for choice in outcome.choices
        ],
    }


def _from_payload(payload: Any) -> InstallationOutcome | None:
    if not isinstance(payload, dict):
        return None
    try:
        state = InstallState(payload["state"])
    except KeyError, ValueError:
        return None
    choices = payload.get("choices")
    return InstallationOutcome(
        state=state,
        installation_id=payload.get("installation_id"),
        choices=tuple(
            InstallationChoice(**choice)
            for choice in (choices if isinstance(choices, list) else [])
            if isinstance(choice, dict)
        ),
    )


__all__ = ["GithubInstallationReconciler", "RECONCILE_TTL_SECONDS"]
