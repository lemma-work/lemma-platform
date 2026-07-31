"""The surface-platform -> connector registry is the single source of truth."""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.surface_connectors import (
    SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS,
    SURFACE_CONNECTOR_BINDINGS,
    surface_connector_binding,
    surface_connector_id,
)


def test_every_platform_has_a_binding():
    # A new SurfacePlatform must declare its connector here, or binding/credential
    # resolution has nothing to map it to.
    for platform in SurfacePlatform:
        assert platform in SURFACE_CONNECTOR_BINDINGS


def test_teams_maps_to_microsoft_teams():
    assert surface_connector_id(SurfacePlatform.TEAMS) == "microsoft_teams"
    assert surface_connector_binding(SurfacePlatform.TEAMS).provider == "LEMMA"


def test_slack_maps_to_slack():
    assert surface_connector_id(SurfacePlatform.SLACK) == "slack"


def test_self_managed_set_tracks_the_bindings_and_new_teams_id():
    # Renamed teams -> microsoft_teams must be reflected here (derived, not hardcoded).
    assert "microsoft_teams" in SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS
    assert "teams" not in SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS
    # WhatsApp/Telegram/Resend hold static keys; Slack/email are OAuth-refresh.
    assert {"whatsapp", "telegram", "resend"} <= SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS
    assert "slack" not in SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS
    assert "gmail" not in SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS
