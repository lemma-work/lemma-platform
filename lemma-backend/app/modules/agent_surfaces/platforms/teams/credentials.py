"""Finding the Azure tenant a Teams account belongs to.

A leaf module because both halves of the platform service need it and neither
should have to import the other to get it. The spellings a payload may keep it
under are reconciled in `TeamsCredentials.from_payload`.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    TeamsCredentials,
)


def tenant_id_from_credentials(credentials: CredentialsLike) -> str | None:
    """The tenant id, from wherever this credential payload happens to keep it."""
    return TeamsCredentials.of(credentials).tenant_id
