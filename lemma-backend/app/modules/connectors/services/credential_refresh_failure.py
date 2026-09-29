"""What a failed credential refresh means for whoever asked for the credential.

Three answers, and the difference between the first and the last is the one a
client acts on. A grant the provider withdrew is the person's to fix, so it is a
409 naming the account to reconnect. A refusal Lemma already classified passes
through as it is. Anything else -- a 5xx, a dropped connection -- is the
provider being unwell, and stays a 502.
"""

from __future__ import annotations

from typing import NoReturn

from app.core.domain.errors import DomainError
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import (
    ConnectorReauthRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.services.upstream_error_details import (
    upstream_error_details,
)


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
