"""The available-surfaces catalog builder joins registry + connector + native creds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.exc import OperationalError

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


def _claim_repository(conflict=None, *, raises=False) -> AsyncMock:
    repo = AsyncMock()
    if raises:
        repo.get_system_credential_conflict_in_org.side_effect = OperationalError(
            "SELECT 1", {}, Exception("connection reset")
        )
    else:
        repo.get_system_credential_conflict_in_org.return_value = conflict
    return repo


async def test_system_claim_absent_for_platforms_without_a_system_mode(monkeypatch):
    # No SYSTEM mode means there is no shared identity to claim, so the field is
    # None rather than a misleading "available".
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(
        connector_service=_connector_service(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(),
    )
    assert all(surface.system_claim is None for surface in resp.surfaces)


async def test_system_claim_available_when_org_has_not_claimed_it(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(
        connector_service=_connector_service(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(None),
    )
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None
    assert claim.available is True
    assert claim.claimed_by_pod_id is None


async def test_system_claim_names_the_pod_holding_it(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    holder_pod_id = uuid4()
    conflict = SimpleNamespace(pod_id=holder_pod_id, name="whatsapp")
    monkeypatch.setattr(mod, "AgentSurfaceEntity", SimpleNamespace)
    resp = await build_available_surfaces(
        connector_service=_connector_service(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(conflict),
    )
    claim = _by_platform(resp)[SurfacePlatform.WHATSAPP].system_claim
    assert claim is not None
    assert claim.available is False
    assert claim.claimed_by_pod_id == holder_pod_id
    assert claim.claimed_by_surface_name == "whatsapp"


async def test_system_claim_degrades_to_available_when_lookup_fails(monkeypatch):
    # A catalog read must never fail on a repository hiccup; the write path
    # still enforces the claim, so optimistic is the safe direction.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(
        connector_service=_connector_service(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(raises=True),
    )
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None and claim.available is True


async def test_system_claim_skipped_without_pod_context(monkeypatch):
    # The builder stays usable as a pure registry join.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(connector_service=_connector_service())
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None and claim.available is True


async def test_managed_setup_offered_only_where_a_manager_bot_exists(monkeypatch):
    # Telegram can hand a user their own bot, but only where this deployment has
    # a manager bot configured — otherwise the option must not be offered.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_token", "123:abc", raising=False
    )
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_username", "lemma_manager", raising=False
    )
    surfaces = _by_platform(
        await build_available_surfaces(connector_service=_connector_service())
    )
    assert surfaces[SurfacePlatform.TELEGRAM].managed_setup_available is True
    # It is a Telegram-only path; nothing else claims it.
    assert all(
        surface.managed_setup_available is False
        for platform, surface in surfaces.items()
        if platform is not SurfacePlatform.TELEGRAM
    )


async def test_managed_setup_hidden_without_a_manager_bot(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_token", None, raising=False
    )
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_username", None, raising=False
    )
    surfaces = _by_platform(
        await build_available_surfaces(connector_service=_connector_service())
    )
    assert surfaces[SurfacePlatform.TELEGRAM].managed_setup_available is False


async def test_one_row_per_registry_platform(monkeypatch):
    # The endpoint is registry-driven, so a newly-registered surface (Discord)
    # appears with no builder change.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(connector_service=_connector_service())
    platforms = [s.platform for s in resp.surfaces]
    assert set(platforms) == set(SURFACE_CONNECTOR_BINDINGS)
    assert len(platforms) == len(SURFACE_CONNECTOR_BINDINGS)
