"""Finding the Azure tenant a Teams account belongs to.

A leaf module because both halves of the platform service need it and neither
should have to import the other to get it.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.platforms.common import payload_any


def tenant_id_from_credentials(credentials: dict[str, Any]) -> str | None:
    """The tenant id, from wherever this credential payload happens to keep it."""
    user_data = credentials.get("user_data") or {}
    raw = credentials.get("raw_response") or {}
    return (
        payload_any(user_data, "tenant_id", "tid")
        or payload_any(raw, "tenant_id", "tid")
        or payload_any(credentials, "tenant_id", "tid")
    ) or None
