"""What a failed credential refresh means for whoever asked for the credential.

Three answers, and the difference between the first and the last is the one a
client acts on. A grant the provider withdrew is the person's to fix, so it is a
409 naming the account to reconnect. A refusal Lemma already classified passes
through as it is. Anything else -- a 5xx, a dropped connection -- is the
provider being unwell, and stays a 502.

One refusal is not what it says. Where a refresh token works once, the second of
two refreshes of an account is told its token is no good, in the words a
withdrawn grant gets (`renewed_elsewhere`).
"""

from __future__ import annotations

from typing import NoReturn

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.domain.errors import (
    ConnectorReauthRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.services.upstream_error_details import (
    upstream_error_details,
)

logger = get_logger(__name__)


def reauth_required(
    account: AccountEntity, reason: str
) -> ConnectorReauthRequiredError:
    return ConnectorReauthRequiredError(
        reason=reason, account_id=account.id, connector_id=account.connector_id
    )


def raise_refresh_failure(account: AccountEntity, exc: Exception) -> NoReturn:
    if isinstance(exc, ConnectorReauthRequiredError):
        # Raised by the auth provider, which never holds the account.
        raise reauth_required(account, exc.reason) from exc
    if isinstance(exc, DomainError):
        raise exc
    raise OAuthWorkflowError(
        "Unable to refresh connector credentials.",
        details=upstream_error_details(exc),
    ) from exc


async def renewed_elsewhere(
    accounts: AccountRepositoryPort, account: AccountEntity, used: OAuthCredentials
) -> dict[str, object] | None:
    """The stored credentials, when another refresh replaced them while this
    one was out; ``None`` when the row still holds what was sent.

    Asked when a refresh is refused as a withdrawn grant. Two refreshes of one
    account cannot both succeed where a refresh token is single-use, GitHub's
    among them, and the loser hears exactly what a revoked account hears. The
    row tells them apart: a different refresh token in it means somebody
    else's refresh went through, the grant is alive, and theirs is the
    credential to use -- not an account to mark for a reconnect it does not
    need.
    """
    if not used.refresh_token:
        return None
    stored = await accounts.stored_credentials(account.id)
    if not isinstance(stored, dict):
        return None
    current = stored.get("refresh_token")
    if not current or current == used.refresh_token:
        return None
    logger.info(
        "connectors.credential_refresh_failure.superseded.observed",
        account_id=str(account.id),
        connector_id=account.connector_id,
    )
    return stored
