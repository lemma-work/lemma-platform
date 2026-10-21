"""The Teams admin-consent link: who is given it, and how long it stays good."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services import teams_consent
from app.modules.agent_surfaces.services.surface_consent import SurfaceConsentMixin
from app.modules.agent_surfaces.services.surface_setup_read import SurfaceSetupReadMixin

pytestmark = pytest.mark.asyncio


class _NonceStore:
    """The four Redis operations the nonce store uses, compare-and-delete included.

    Not fakeredis: the compare-and-delete is a Lua script, which it cannot run
    without an optional dependency.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set_raw(self, suffix, payload, *, ttl_seconds=None):
        self.values[suffix] = payload

    async def set_raw_if_absent(self, suffix, payload, *, ttl_seconds=None):
        if suffix in self.values:
            return False
        self.values[suffix] = payload
        return True

    async def get_raw(self, suffix):
        return self.values.get(suffix)

    async def delete_if_value(self, suffix, expected):
        if self.values.get(suffix) != expected:
            return False
        del self.values[suffix]
        return True


@pytest.fixture
def nonce_store(monkeypatch) -> _NonceStore:
    store = _NonceStore()
    monkeypatch.setattr(teams_consent, "_get_cache", lambda: store)
    monkeypatch.setattr(teams_consent.settings, "api_url", "https://api.example.test")
    return store


def _state_nonce(url: str) -> tuple[str, str]:
    state = url.split("state=")[1].split("&")[0]
    issued_id, _, nonce = state.partition("%3A")
    return issued_id, nonce


async def test_teams_consent_callback_requires_the_nonce_it_issued(nonce_store):
    """The callback is unauthenticated and the surface id is not a secret.

    It is shown to every pod member who starts Teams setup and never rotates,
    so possession of it must not be enough to activate the surface and pin its
    tenant binding.
    """
    surface_id = uuid4()

    assert not await teams_consent.consume_nonce(surface_id, "guessed")
    assert not await teams_consent.consume_nonce(surface_id, "")

    url = await teams_consent.build_consent_url(surface_id, "tenant-123")
    issued_id, nonce = _state_nonce(url)
    assert issued_id == str(surface_id)
    assert nonce

    # Wrong nonce for a real surface is still refused.
    assert not await teams_consent.consume_nonce(surface_id, "wrong")
    # The issued one works exactly once, so a replayed callback loses.
    assert await teams_consent.consume_nonce(surface_id, nonce)
    assert not await teams_consent.consume_nonce(surface_id, nonce)


async def test_reading_setup_again_does_not_void_the_link_already_sent(nonce_store):
    """An editor copies the link to their administrator; someone reopens setup."""
    surface_id = uuid4()

    sent = await teams_consent.build_consent_url(surface_id, "tenant-123")
    reopened = await teams_consent.build_consent_url(surface_id, "tenant-123")

    assert reopened == sent
    assert await teams_consent.consume_nonce(surface_id, _state_nonce(sent)[1])


async def test_a_spent_link_is_replaced_by_a_new_one(nonce_store):
    surface_id = uuid4()
    first = await teams_consent.build_consent_url(surface_id, "tenant-123")
    assert await teams_consent.consume_nonce(surface_id, _state_nonce(first)[1])

    second = await teams_consent.build_consent_url(surface_id, "tenant-123")

    assert second != first


class _SetupHarness(SurfaceSetupReadMixin, SurfaceConsentMixin):
    pass


def _pending_teams_surface() -> AgentSurfaceEntity:
    surface = AgentSurfaceEntity.create(
        pod_id=uuid4(),
        surface_type=SurfacePlatform.TEAMS,
        agent_id=uuid4(),
        config=SurfaceConfig(),
        account_id=uuid4(),
        external_tenant_id="tenant-123",
    )
    assert surface.status is AgentSurfaceStatus.PENDING_ADMIN_CONSENT
    return surface


def _harness(surface: AgentSurfaceEntity) -> _SetupHarness:
    harness = _SetupHarness()
    harness.get_surface_by_name_in_pod = AsyncMock(return_value=surface)
    harness.get_platform_setup_guide = lambda platform: None
    harness._surface_uses_org_custom_app = AsyncMock(return_value=False)
    harness._credential_resolver = None
    # Answers "already granted" if asked, and records any write: the setup read
    # must ask neither.
    harness._check_admin_consent_granted = AsyncMock(return_value=True)
    harness.surface_repository = SimpleNamespace(update=AsyncMock())
    return harness


async def test_a_reader_who_cannot_change_the_surface_gets_no_consent_link(
    nonce_store,
):
    surface = _pending_teams_surface()

    setup = await _harness(surface).get_surface_setup_by_name(
        pod_id=surface.pod_id, name=surface.name, reveal_secrets=False, connection=None
    )

    assert setup["admin_consent"]["required"] is True
    assert setup["admin_consent"]["granted"] is False
    assert setup["admin_consent"]["consent_url"] is None
    assert await nonce_store.get_raw(str(surface.id)) is None


async def test_an_editor_gets_the_consent_link(nonce_store):
    surface = _pending_teams_surface()

    setup = await _harness(surface).get_surface_setup_by_name(
        pod_id=surface.pod_id, name=surface.name, reveal_secrets=True, connection=None
    )

    assert "adminconsent" in setup["admin_consent"]["consent_url"]


async def test_reading_setup_writes_nothing_to_the_surface(nonce_store):
    surface = _pending_teams_surface()
    harness = _harness(surface)

    await harness.get_surface_setup_by_name(
        pod_id=surface.pod_id, name=surface.name, reveal_secrets=True, connection=None
    )

    harness.surface_repository.update.assert_not_awaited()
    assert surface.status is AgentSurfaceStatus.PENDING_ADMIN_CONSENT


async def test_a_surface_whose_tenant_already_consented_starts_active():
    surface = _pending_teams_surface()
    harness = _harness(surface)

    await harness.activate_if_tenant_consented(surface)

    assert surface.status is AgentSurfaceStatus.ACTIVE
