from __future__ import annotations

from typing import Any
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountBindingPort,
    SurfaceAccountInfo,
    SurfaceAccountPort,
)


class SurfaceAccountBindingResolver(SurfaceAccountBindingPort):
    """Validate the connected account matches the platform and derive the
    non-secret routing identity (tenant/workspace/bot ids)."""

    def __init__(self, account_port: SurfaceAccountPort):
        self._account_port = account_port

    async def resolve_binding(
        self,
        platform: SurfacePlatform,
        account_id: UUID | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (external_tenant_id, external_workspace_id, surface_identity_id)."""
        if platform is SurfacePlatform.SLACK:
            return await self._resolve_slack(account_id)
        if platform is SurfacePlatform.TEAMS:
            tenant_id = await self._resolve_teams(account_id)
            return tenant_id, None, None
        if platform is SurfacePlatform.RESEND:
            # System-credentialed by default (no account); a CUSTOM Resend
            # connector account is optional.
            if account_id is not None:
                await self._require_account_app(
                    account_id, label="Resend", expected_connector_id="resend"
                )
            return None, None, None
        if platform.is_email:
            await self._require_account_app(
                account_id,
                label=platform.value.title(),
                expected_connector_id=platform.value.lower(),
            )
            return None, None, None
        if account_id is not None:
            # WhatsApp/Telegram accept either a connected account or system creds.
            await self._require_account_app(
                account_id,
                label=platform.value.title(),
                expected_connector_id=platform.value.lower(),
            )
        return None, None, None

    async def _get_account(self, account_id: UUID, label: str) -> SurfaceAccountInfo:
        account = await self._account_port.get_account(account_id)
        if account is None:
            raise AgentSurfaceValidationError(f"{label} account '{account_id}' not found")
        return account

    async def _require_account_app(
        self,
        account_id: UUID | None,
        *,
        label: str,
        expected_connector_id: str,
    ) -> SurfaceAccountInfo:
        if account_id is None:
            raise AgentSurfaceValidationError(f"{label} surfaces require account_id")
        account = await self._get_account(account_id, label)
        if account.connector_id.lower() != expected_connector_id:
            raise AgentSurfaceValidationError(
                f"{label} surfaces require a connected {expected_connector_id} account"
            )
        return account

    async def _resolve_slack(
        self,
        account_id: UUID | None,
    ) -> tuple[str | None, str | None, str | None]:
        account = await self._require_account_app(
            account_id,
            label="Slack",
            expected_connector_id="slack",
        )
        raw_response = account.credentials.get("raw_response") or {}
        workspace_id = (
            _nested_str(raw_response, "team", "id")
            or _str_or_none(raw_response.get("team_id"))
            or _str_or_none(raw_response.get("team"))
        )
        bot_user_id = _str_or_none(raw_response.get("bot_user_id"))
        if not bot_user_id:
            raise AgentSurfaceValidationError(
                "Slack account credentials missing raw_response.bot_user_id"
            )
        return None, workspace_id, bot_user_id

    async def _resolve_teams(self, account_id: UUID | None) -> str | None:
        account = await self._require_account_app(
            account_id,
            label="Teams",
            expected_connector_id="teams",
        )
        raw_response = account.credentials.get("raw_response") or {}
        user_data = account.credentials.get("user_data") or {}
        tenant_id = (
            _str_or_none(user_data.get("tenant_id"))
            or _str_or_none(user_data.get("tid"))
            or _str_or_none(raw_response.get("tenant_id"))
            or _str_or_none(raw_response.get("tid"))
        )
        if not tenant_id:
            raise AgentSurfaceValidationError(
                "Could not resolve tenant_id from the connected Teams account. "
                "Please reconnect your Teams account."
            )
        return tenant_id


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _nested_str(data: dict[str, Any], *path: str) -> str | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _str_or_none(current)
