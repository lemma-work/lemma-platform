"""Replacing an account's credential without replacing the account.

There was no way to do this, so the UI did the only thing it could: delete the
account, then create a new one. Two problems with that, and the second is the
reason this exists rather than a rollback.

A failed create leaves nothing behind. A mistyped key, a provider that is down,
a duplicate-identity race -- and the working account is already gone, deletion
having revoked it upstream on the way out. The person is told "Failed to save
credentials" and not that they no longer have an account.

And a successful create still issues a NEW id. Every schedule, surface, agent
grant and pod-bundle variable pinned to the old one is left pointing at a row
that does not exist. `install_update` goes to some length to avoid exactly this
on the install side -- "the row, its id, and every reference to it survive" --
and the account side had no equivalent.

Rotation in place is also the only shape that works for the common case. The
credential usually belongs to the same upstream identity, so create-then-delete
collides with `uq_accounts_provider_identity` before the old row is gone.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.services.account_credentials import (
    validated_account_credentials,
)
from app.modules.connectors.services.install_service_seam import (
    InstallServiceSeam,
)


async def rotate_account_credentials(
    service: InstallServiceSeam,
    *,
    account_id: UUID,
    user_id: UUID,
    organization_id: UUID,
    credentials: dict[str, Any],
) -> AccountEntity:
    """Store a new credential on an existing account, keeping its id.

    Only for credential-managed accounts. An OAuth account is re-authorized by
    going through the provider again, which mints its own credential -- there
    is nothing for a caller to supply.
    """
    account = await service.get_account(account_id, user_id, organization_id)
    auth_config = await service._resolve_auth_config(
        organization_id=organization_id,
        auth_config_id=account.auth_config_id,
    )
    connector = await service.get_connector(account.connector_id)
    auth_install = service._resolve_auth_install(connector, auth_config)
    if auth_install.auth_scheme.value == "OAUTH2":
        raise ConnectorValidationError(
            "This account signs in through the provider. Reconnect it instead "
            "of supplying a credential."
        )

    # Validated through the entity, not assigned raw. `AccountEntity` does not
    # set `validate_assignment`, so a plain assignment leaves a dict on the
    # model where creation -- which validates at construction -- would have left
    # a typed credential. The repository serialises both, so nothing broke, but
    # the two paths storing different shapes for the same connector is the kind
    # of difference that surfaces much later as a puzzling `AttributeError`.
    account.credentials = AccountEntity.model_validate(
        {
            **account.model_dump(),
            "credentials": validated_account_credentials(
                connector, auth_config.kind, credentials
            ),
        }
    ).credentials
    # A credential that was rejected is what put the account here, and a new one
    # deserves the benefit of the doubt: the next call decides.
    account.status = AccountStatus.CONNECTED
    updated = await service.account_repository.update(account)
    await service.uow.commit()
    return updated
