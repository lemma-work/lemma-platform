"""The connectors landing page's one call: installs and my accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.modules.connectors.services.connector_service import ConnectorService


async def connector_status(
    service: ConnectorService, user_id: UUID, organization_id: UUID
) -> dict[str, list[dict[str, str | None]]]:
    configs, _ = await service.auth_config_repository.list_by_org(
        organization_id, limit=200
    )
    accounts, _ = await service.account_repository.list_by_user_and_org(
        user_id, organization_id, limit=200
    )

    # One query for every title, not one per distinct connector: this is
    # the connectors landing page's single call, and an org with installs
    # of a dozen connectors was paying a round trip each on every load.
    app_ids = {c.connector_id for c in configs} | {a.connector_id for a in accounts}
    app_titles = await service.connector_repository.titles_for(sorted(app_ids))

    return {
        "installed": [
            {
                "name": c.name,
                "connector_id": c.connector_id,
                "title": app_titles.get(c.connector_id),
                "status": c.status.value
                if hasattr(c.status, "value")
                else str(c.status),
                "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind),
            }
            for c in configs
        ],
        "accounts": [
            {
                "id": str(a.id),
                "connector_id": a.connector_id,
                "title": app_titles.get(a.connector_id),
                "email": a.email,
                "status": a.status.value
                if hasattr(a.status, "value")
                else str(a.status),
            }
            for a in accounts
        ],
    }
