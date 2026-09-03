from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.core.domain.entity import Entity
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.connector import (
    AuthProvider,
    ConnectorKind,
    kind_to_provider,
    provider_to_kind,
)


class AuthConfigStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AuthConfigSource(str, enum.Enum):
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"
    ORG_CUSTOM = "ORG_CUSTOM"


# Composio brokers every one of its toolkits through Lemma's own Composio
# account -- one process-global ``COMPOSIO_API_KEY`` (connectors/config.py).
# There is no per-org Composio key, so a Composio install is always
# SYSTEM_DEFAULT.
#
# Two layers enforce this, and they are not redundant: the service check runs
# on create, the kind installer also runs on the update path. They share these
# constants so the two can't drift into saying different things.
COMPOSIO_SYSTEM_CREDENTIALS_ONLY = (
    "Composio installs use Lemma's Composio credentials; org-supplied "
    "credentials are not supported."
)
COMPOSIO_ORG_CUSTOM_REASON = "org_custom_not_supported_for_composio"


class AuthConfigEntity(Entity):
    """One organization's install of a connector.

    ``kind`` is the single runtime discriminator -- it decides which plugin
    authenticates, discovers and executes. An org may hold many installs of the
    same connector (two Slack apps, three MCP servers); they are told apart by
    ``name``, and ``is_default`` picks the one that legacy callers addressing a
    bare ``connector_id`` resolve to.
    """

    organization_id: UUID
    connector_id: str
    kind: ConnectorKind = ConnectorKind.PACKAGE
    config_source: AuthConfigSource = AuthConfigSource.SYSTEM_DEFAULT
    status: AuthConfigStatus = AuthConfigStatus.ACTIVE
    name: str
    is_default: bool = False
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    created_by_user_id: UUID | None = None
    updated_by_user_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_provider_fields(cls, data: Any) -> Any:
        """Accept the pre-kind vocabulary from callers not yet migrated.

        ``provider=`` maps to ``kind`` only when ``kind`` is absent, and
        ``LEMMA`` degrades to ``PACKAGE`` -- correct for every install that
        existed before kinds, since http/sql/mcp did not ship. Callers that know
        the real kind pass it explicitly and this leaves them alone.
        """
        if not isinstance(data, dict):
            return data
        if data.get("kind") is None and data.get("provider") is not None:
            data = {**data, "kind": provider_to_kind(data["provider"])}
        if data.get("config") is None and data.get("provider_config") is not None:
            data = {**data, "config": data["provider_config"]}
        return data

    @property
    def provider(self) -> AuthProvider:
        """Deprecated alias derived from :attr:`kind`."""
        return kind_to_provider(self.kind)

    @property
    def provider_config(self) -> dict[str, Any] | None:
        """Deprecated alias for :attr:`config`."""
        return self.config

    @property
    def uses_composio(self) -> bool:
        return self.kind is ConnectorKind.COMPOSIO

    @property
    def uses_native(self) -> bool:
        return self.kind is not ConnectorKind.COMPOSIO


def reject_if_disabled(auth_config: AuthConfigEntity) -> None:
    """Refuse to use an install an admin has switched off.

    `status` is the only way short of deletion to stop an install being used,
    and deletion cascades away every account on it -- so an admin who disables
    a compromised install reasonably believes it is off. It has to be off on
    every path that reaches the provider, not only the ones that look the
    install up by name.

    Named rather than a 404: the caller supplied an id for an install they can
    already see in their own organization, so there is nothing to withhold, and
    "not found" would send them looking for a row that is right there.
    """
    if auth_config.status is AuthConfigStatus.DISABLED:
        raise ConnectorValidationError(
            f"The connector install '{auth_config.name}' is disabled. "
            "Re-enable it before connecting or running operations against it.",
            details={"reason": "install_disabled"},
        )
