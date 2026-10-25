"""What the OAuth callback does with the GitHub installation it came back with.

Audit H4. An installation another organization holds is refused outright. One
nobody holds yet is bound only to its owner on GitHub's side; anyone else still
connects their own GitHub account -- the OAuth grant is theirs -- just without
the installation, and the connect request says so, so the callback reports the
same PENDING_APPROVAL a reconcile does. A later reconcile picks the
installation up once an admin has bound it.

Out of `oauth_callback` so that file stays one saga under the size ceiling.
"""

from __future__ import annotations

from dataclasses import Field, replace
from typing import ClassVar, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.connectors.domain.connect_request import ConnectRequestEntity
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationNotAdministeredError,
)
from app.modules.connectors.services.auth.github_bindings import (
    claim_installation,
    is_github,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    with_installation_needing_an_admin,
    without_spent_secrets,
)

logger = get_logger(__name__)


class _CarriesInstallation(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[object]]]

    @property
    def external_ref(self) -> str | None: ...

    @property
    def installation_administered(self) -> bool: ...


Identity = TypeVar("Identity", bound=_CarriesInstallation)


async def bind_or_leave_for_an_admin(
    session: AsyncSession,
    identity: Identity,
    *,
    connector_id: str,
    organization_id: UUID,
    user_id: UUID,
) -> tuple[Identity, bool]:
    """The identity to store, and whether its installation waits on an admin."""
    if not identity.external_ref or not is_github(connector_id):
        return identity, False
    try:
        await claim_installation(
            session,
            installation_id=identity.external_ref,
            organization_id=organization_id,
            user_id=user_id,
            administered=identity.installation_administered,
        )
    except GithubInstallationNotAdministeredError:
        logger.info(
            "connectors.oauth_callback.installation_left_for_an_admin",
            connector_id=connector_id,
        )
        return replace(identity, external_ref=None), True
    return identity, False


def closed_attributes(
    pending_request: ConnectRequestEntity, *, needs_admin: bool
) -> dict[str, object] | None:
    """A finished request's attributes, marked when its installation waits."""
    attributes = without_spent_secrets(pending_request)
    return with_installation_needing_an_admin(attributes) if needs_admin else attributes
