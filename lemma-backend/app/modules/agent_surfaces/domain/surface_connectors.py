"""Canonical mapping from a surface platform to its connector.

Every surface that runs on a "bring your own bot" account binds a connector
account, and several places need to agree on *which* connector a given platform
maps to and how its credentials behave: account binding (validating the bound
account), credential resolution (OAuth-refresh vs. static key), and connector
identity in general. Historically each of those hardcoded the connector id
inline (``"teams"``, ``platform.value.lower()``, a per-module ``_APP_ID``), which
drifted — most visibly Teams, whose connector is ``microsoft_teams`` (matching
the Composio toolkit slug) even though the platform enum is ``TEAMS``.

This module is the single source of truth. Add a platform here and the binding /
credential / setup layers pick it up instead of re-deriving the id.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


@dataclass(frozen=True)
class SurfaceConnectorBinding:
    """How a surface platform maps to a connector.

    ``connector_id`` — the connector an account bound to this surface must belong
    to (matches the id in the connector catalog / ``lemma_apps_config.json``).
    ``provider`` — the auth provider that connector authenticates through; surface
    bots are LEMMA-native. ``self_managed_credentials`` — the account stores a
    static key/secret rather than a refreshable OAuth token, so credential
    resolution must pass the stored credentials through untouched.
    """

    connector_id: str
    provider: str
    self_managed_credentials: bool


# Keyed by platform; the connector_id is the catalog id (NOT necessarily the
# lowercased platform — Teams is the deliberate exception).
SURFACE_CONNECTOR_BINDINGS: dict[SurfacePlatform, SurfaceConnectorBinding] = {
    SurfacePlatform.SLACK: SurfaceConnectorBinding(
        connector_id="slack", provider="LEMMA", self_managed_credentials=False
    ),
    SurfacePlatform.TEAMS: SurfaceConnectorBinding(
        connector_id="microsoft_teams", provider="LEMMA", self_managed_credentials=True
    ),
    SurfacePlatform.WHATSAPP: SurfaceConnectorBinding(
        connector_id="whatsapp", provider="LEMMA", self_managed_credentials=True
    ),
    SurfacePlatform.TELEGRAM: SurfaceConnectorBinding(
        connector_id="telegram", provider="LEMMA", self_managed_credentials=True
    ),
    SurfacePlatform.GMAIL: SurfaceConnectorBinding(
        connector_id="gmail", provider="LEMMA", self_managed_credentials=False
    ),
    SurfacePlatform.OUTLOOK: SurfaceConnectorBinding(
        connector_id="outlook", provider="LEMMA", self_managed_credentials=False
    ),
    SurfacePlatform.RESEND: SurfaceConnectorBinding(
        connector_id="resend", provider="LEMMA", self_managed_credentials=True
    ),
}


def surface_connector_binding(platform: SurfacePlatform) -> SurfaceConnectorBinding:
    """The connector binding for a platform. Raises ``KeyError`` for an unmapped
    platform — a new SurfacePlatform must declare its connector here."""
    return SURFACE_CONNECTOR_BINDINGS[platform]


def surface_connector_id(platform: SurfacePlatform) -> str:
    """The connector id an account for ``platform`` must belong to."""
    return SURFACE_CONNECTOR_BINDINGS[platform].connector_id


# Connectors whose accounts hold service-level/static credentials (no OAuth
# refresh flow). Derived from the registry so it can't drift from the bindings.
SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS: frozenset[str] = frozenset(
    binding.connector_id
    for binding in SURFACE_CONNECTOR_BINDINGS.values()
    if binding.self_managed_credentials
)
