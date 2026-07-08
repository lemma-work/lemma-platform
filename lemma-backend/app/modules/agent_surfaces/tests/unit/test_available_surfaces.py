"""The available-surfaces catalog builder joins registry + connector + native creds."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
    surface_connector_id,
)
from app.modules.agent_surfaces.services import available_surfaces_builder as mod
from app.modules.agent_surfaces.services.available_surfaces_builder import (
    build_available_surfaces,
)
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorEntity,
    LemmaProviderCapability,
)
from app.modules.connectors.domain.errors import ConnectorNotFoundError

_CUSTOM = SurfaceCredentialMode.CUSTOM
_SYSTEM = SurfaceCredentialMode.SYSTEM
_NATIVE = {SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM, SurfacePlatform.RESEND}


def _connector(connector_id: str, *, is_active=True, capability=None) -> ConnectorEntity:
    return ConnectorEntity(
        id=connector_id,
        title=connector_id.replace("_", " ").title(),
        icon=f"{connector_id}.png",
        is_active=is_active,
        provider_capabilities=[] if capability is None else [capability],
    )


def _default_cap() -> LemmaProviderCapability:
    return LemmaProviderCapability(
        auth_scheme=AuthScheme.OAUTH2, system_default_available=True
    )


def _connector_service(
    *, missing=(), inactive=(), no_lemma=(), capability=None
) -> AsyncMock:
    cap = capability or _default_cap()

    def _get(connector_id: str) -> ConnectorEntity:
        if connector_id in missing:
            raise ConnectorNotFoundError(connector_id)
        if connector_id in no_lemma:
            return _connector(connector_id, capability=None)  # no LEMMA capability
        if connector_id in inactive:
            return _connector(connector_id, is_active=False, capability=cap)
        return _connector(connector_id, capability=cap)

    svc = AsyncMock()
    svc.get_connector.side_effect = _get
    return svc


def _by_platform(resp) -> dict[SurfacePlatform, object]:
    return {s.platform: s for s in resp.surfaces}


async def test_modes_reflect_native_credentials(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    surfaces = _by_platform(
        await build_available_surfaces(connector_service=_connector_service())
    )
    for platform in _NATIVE:
        assert surfaces[platform].supported_credential_modes == [_CUSTOM, _SYSTEM]
    for platform in (
        SurfacePlatform.SLACK,
        SurfacePlatform.TEAMS,
        SurfacePlatform.GMAIL,
        SurfacePlatform.OUTLOOK,
    ):
        assert surfaces[platform].supported_credential_modes == [_CUSTOM]


async def test_modes_drop_system_when_no_native_credentials(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(connector_service=_connector_service())
    for surface in resp.surfaces:
        assert surface.supported_credential_modes == [_CUSTOM]


async def test_connect_descriptor_maps_capability(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    cap = LemmaProviderCapability(
        auth_scheme=AuthScheme.API_KEY,
        credential_schema={"type": "object"},
        auth_config_schema={"x": 1},
        system_default_available=True,
        supports_org_custom_oauth=True,
    )
    resp = await build_available_surfaces(
        connector_service=_connector_service(capability=cap)
    )
    teams = _by_platform(resp)[SurfacePlatform.TEAMS]
    assert teams.connector_id == "microsoft_teams"
    assert teams.connector_available is True
    assert teams.connect is not None
    assert teams.connect.auth_scheme == AuthScheme.API_KEY
    assert teams.connect.credential_schema == {"type": "object"}
    assert teams.connect.auth_config_schema == {"x": 1}
    assert teams.connect.system_oauth_available is True
    assert teams.connect.supports_org_custom_oauth is True


async def test_missing_connector_marked_unavailable(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    telegram = surface_connector_id(SurfacePlatform.TELEGRAM)
    resp = await build_available_surfaces(
        connector_service=_connector_service(missing={telegram})
    )
    surface = _by_platform(resp)[SurfacePlatform.TELEGRAM]
    assert surface.connector_available is False
    assert surface.connect is None
    # Registry-derived fields survive so the platform is still visible.
    assert surface.connector_id == telegram
    assert surface.supported_credential_modes == [_CUSTOM]
    assert len(resp.surfaces) == len(SURFACE_CONNECTOR_BINDINGS)


async def test_inactive_connector_marked_unavailable(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    slack = surface_connector_id(SurfacePlatform.SLACK)
    resp = await build_available_surfaces(
        connector_service=_connector_service(inactive={slack})
    )
    surface = _by_platform(resp)[SurfacePlatform.SLACK]
    assert surface.connector_available is False
    assert surface.connect is None


async def test_no_lemma_capability_does_not_raise(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    gmail = surface_connector_id(SurfacePlatform.GMAIL)
    resp = await build_available_surfaces(
        connector_service=_connector_service(no_lemma={gmail})
    )
    surface = _by_platform(resp)[SurfacePlatform.GMAIL]
    assert surface.connector_available is False
    assert surface.connect is None


async def test_one_row_per_registry_platform(monkeypatch):
    # The endpoint is registry-driven, so a newly-registered surface (Discord)
    # appears with no builder change.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(connector_service=_connector_service())
    platforms = [s.platform for s in resp.surfaces]
    assert set(platforms) == set(SURFACE_CONNECTOR_BINDINGS)
    assert len(platforms) == len(SURFACE_CONNECTOR_BINDINGS)
