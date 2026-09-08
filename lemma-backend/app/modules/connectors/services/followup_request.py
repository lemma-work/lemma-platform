"""The second leg of a connect that could not finish in one round trip.

A module function taking the service rather than a method on it, the same shape
as `handle_oauth_callback`: this is one self-contained step of the connect
saga, and `ConnectorService` is already the largest file in the module.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    followup_attributes,
    with_return_path,
)


async def peek_connect_request(service: Any, state: str) -> Any:
    """Read a connect request without spending it.

    The callback needs two facts that live on the request and are gone the
    moment the exchange claims it: whether this leg is the return from an
    install, and where the person was when they started. Reading is not
    claiming -- `claim_pending_by_state` remains the only thing that spends a
    state, and still does it in one UPDATE.
    """
    return await service.connect_request_repository.get_by_state(state)


async def initiate_followup_request(
    service: Any,
    account: AccountEntity,
    *,
    link: Callable[[str], str | None],
    return_to: str | None = None,
) -> str | None:
    """A second leg for an account that is connected but not finished.

    Some providers cannot finish in one round trip. A GitHub App issues a
    working token the moment somebody authorizes, but that token reaches no
    repository until the App is *installed*, and installing is a separate visit
    that comes back through the same callback carrying an `installation_id`.
    Without a `state` on that link the callback has no request to claim and
    rejects the only redirect that ever names the installation -- which is
    precisely how the flow used to dead-end.

    `link` builds the destination from the freshly minted state. Returning None
    means the deployment cannot say where to send anyone (no App slug
    configured), and then there is no request worth storing either.

    No PKCE verifier: the provider builds this leg's authorize step itself, so
    there is no challenge to carry. `followup_attributes` says what stands in
    its place, and why that is not merely a weaker check.
    """
    state = secrets.token_urlsafe(32)
    url = link(state)
    if url is None:
        return None
    connect_request = ConnectRequestEntity(
        user_id=account.user_id,
        organization_id=account.organization_id,
        auth_config_id=account.auth_config_id,
        connector_id=account.connector_id,
        authorization_url=url,
        status=ConnectRequestStatus.PENDING,
        attributes=with_return_path(
            followup_attributes(
                state,
                account_id=account.id,
                provider_account_id=account.provider_account_id,
            ),
            return_to,
        ),
    )
    await service.connect_request_repository.create(connect_request)
    await service.uow.commit()
    return url
