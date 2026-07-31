from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent_surfaces.api.schemas import AgentSurfaceResponse, SurfaceReach
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)
from app.modules.agent_surfaces.services import surface_reach_resolver
from app.modules.agent_surfaces.services.surface_reach_resolver import (
    SurfaceReachResolver,
)


def _surface(**overrides) -> AgentSurfaceEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "name": "slack",
        "agent_id": uuid4(),
        "surface_type": SurfacePlatform.SLACK,
        "account_id": uuid4(),
        "config": SurfaceConfig(),
        "surface_identity_id": "U123BOT",
    }
    payload.update(overrides)
    return AgentSurfaceEntity(**payload)


class FakeCredentialResolver:
    def __init__(self, credentials: dict | None = None):
        self.credentials = credentials or {"bot_token": "xoxb-test"}
        self.calls = 0

    async def for_surface(self, surface, **kwargs):
        self.calls += 1
        return self.credentials


class FakeAccountRepository:
    def __init__(self, display_name: str | None = "Support Bot Account"):
        self._display_name = display_name

    async def get(self, account_id):
        return SimpleNamespace(display_name=self._display_name)


class FakeConnectorService:
    def __init__(self, display_name: str | None = "Support Bot Account"):
        self.account_repository = FakeAccountRepository(display_name)


class FakeSurfaceRepository:
    def __init__(self):
        self.updated: list[AgentSurfaceEntity] = []

    async def update(self, surface):
        self.updated.append(surface)
        return surface


# ---------------------------------------------------------------------------
# 1. Per-platform handle resolution
# ---------------------------------------------------------------------------


async def test_slack_handle_resolved_and_persisted(monkeypatch):
    async def fake_display_name(self, user_id):
        return "lemma-bot"

    monkeypatch.setattr(
        SlackPlatformService, "get_user_display_name", fake_display_name
    )
    surface = _surface(surface_type=SurfacePlatform.SLACK)
    cred = FakeCredentialResolver()
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=cred,
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )

    assert reach.handle == "lemma-bot"
    assert surface.surface_identity_username == "lemma-bot"
    assert repo.updated == [surface]


async def test_telegram_handle_prefixes_at_and_persists(monkeypatch):
    async def fake_username(self):
        return "lemma_bot"

    monkeypatch.setattr(TelegramPlatformService, "get_bot_username", fake_username)
    surface = _surface(surface_type=SurfacePlatform.TELEGRAM)
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver({"bot_token": "123:abc"}),
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )

    assert reach.handle == "@lemma_bot"
    assert surface.surface_identity_username == "@lemma_bot"
    assert repo.updated == [surface]


async def test_teams_handle_from_graph(monkeypatch):
    async def fake_teams_handle(self, surface):
        # Simulate a successful graph servicePrincipal lookup at the seam.
        return "Lemma Teams Bot"

    monkeypatch.setattr(
        SurfaceReachResolver, "_teams_handle", fake_teams_handle
    )
    surface = _surface(
        surface_type=SurfacePlatform.TEAMS,
        surface_identity_id=None,
        external_tenant_id="tenant-1",
    )
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )

    assert reach.handle == "Lemma Teams Bot"
    assert surface.surface_identity_username == "Lemma Teams Bot"


async def test_teams_handle_config_fallback(monkeypatch):
    # Graph token unavailable → falls back to configured bot app name.
    async def fake_token(tenant_id):
        return None

    monkeypatch.setattr(surface_reach_resolver, "get_graph_token", fake_token)
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_id", "app-123"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_name", "Lemma (config)"
    )
    surface = _surface(
        surface_type=SurfacePlatform.TEAMS,
        surface_identity_id=None,
        external_tenant_id="tenant-1",
    )
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )

    assert reach.handle == "Lemma (config)"
    assert surface.surface_identity_username == "Lemma (config)"


async def test_whatsapp_handle_resolves_display_phone_and_persists():
    surface = _surface(
        surface_type=SurfacePlatform.WHATSAPP,
        surface_identity_id=None,
    )
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(
            {
                "access_token": "wa-token",
                "phone_number_id": "PN42",
                "display_phone_number": "+1 555 0100",
            }
        ),
        connector_service=FakeConnectorService("WhatsApp Account Label"),
        surface_repository=repo,
    )

    assert reach.handle == "+1 555 0100"
    assert surface.surface_identity_username == "+1 555 0100"
    assert repo.updated == [surface]


async def test_whatsapp_falls_back_to_account_display_name_when_number_unavailable():
    surface = _surface(
        surface_type=SurfacePlatform.WHATSAPP, surface_identity_id=None
    )
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService("WhatsApp +1 555"),
        surface_repository=repo,
    )

    assert reach.handle == "WhatsApp +1 555"
    # Fallback is not a live-resolved username → nothing persisted.
    assert repo.updated == []


async def test_gmail_falls_back_to_account_display_name():
    surface = _surface(
        surface_type=SurfacePlatform.GMAIL,
        surface_identity_id=None,
        surface_identity_email="bot@example.com",
    )
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService("Gmail Mailbox"),
        surface_repository=repo,
    )

    assert reach.handle == "Gmail Mailbox"
    assert repo.updated == []


async def test_outlook_falls_back_to_account_display_name():
    surface = _surface(
        surface_type=SurfacePlatform.OUTLOOK, surface_identity_id=None
    )

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService("Outlook Mailbox"),
        surface_repository=FakeSurfaceRepository(),
    )

    assert reach.handle == "Outlook Mailbox"


async def test_resend_falls_back_to_surface_email():
    surface = _surface(
        surface_type=SurfacePlatform.RESEND,
        account_id=None,
        surface_identity_id=None,
        surface_identity_email="pod-abc@ops.lemma.work",
    )

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService(),
        surface_repository=FakeSurfaceRepository(),
    )

    assert reach.handle == "pod-abc@ops.lemma.work"
    assert reach.email == "pod-abc@ops.lemma.work"


# ---------------------------------------------------------------------------
# 2. Lazy write-through
# ---------------------------------------------------------------------------


async def test_lazy_write_through_second_read_makes_no_call(monkeypatch):
    call_count = {"n": 0}

    async def fake_display_name(self, user_id):
        call_count["n"] += 1
        return "lemma-bot"

    monkeypatch.setattr(
        SlackPlatformService, "get_user_display_name", fake_display_name
    )
    surface = _surface(surface_type=SurfacePlatform.SLACK)
    cred = FakeCredentialResolver()
    repo = FakeSurfaceRepository()
    resolver = SurfaceReachResolver()

    first = await resolver.resolve(
        surface,
        credential_resolver=cred,
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )
    assert first.handle == "lemma-bot"
    assert call_count["n"] == 1
    assert cred.calls == 1
    assert len(repo.updated) == 1

    # Second read: surface now HAS surface_identity_username → no platform/cred
    # call, no new persist, returns the stored value.
    second = await resolver.resolve(
        surface,
        credential_resolver=cred,
        connector_service=FakeConnectorService(),
        surface_repository=repo,
    )
    assert second.handle == "lemma-bot"
    assert call_count["n"] == 1
    assert cred.calls == 1
    assert len(repo.updated) == 1


# ---------------------------------------------------------------------------
# 3. Best-effort: platform raises → fall back, no exception
# ---------------------------------------------------------------------------


async def test_platform_failure_falls_back_to_account_display_name(monkeypatch):
    async def boom(self, user_id):
        raise RuntimeError("slack down")

    monkeypatch.setattr(SlackPlatformService, "get_user_display_name", boom)
    surface = _surface(surface_type=SurfacePlatform.SLACK)
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService("Fallback Account"),
        surface_repository=repo,
    )

    assert reach.handle == "Fallback Account"
    # No live username resolved → nothing persisted.
    assert repo.updated == []


async def test_no_handle_when_everything_missing(monkeypatch):
    async def none_name(self, user_id):
        return None

    monkeypatch.setattr(SlackPlatformService, "get_user_display_name", none_name)
    surface = _surface(
        surface_type=SurfacePlatform.SLACK,
        account_id=None,
        surface_identity_email=None,
    )

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=None,
        surface_repository=FakeSurfaceRepository(),
    )

    assert reach.handle is None
    assert reach.email is None


# ---------------------------------------------------------------------------
# 4. reach.email + response exposure
# ---------------------------------------------------------------------------


async def test_reach_email_matches_surface_identity_email(monkeypatch):
    async def fake_display_name(self, user_id):
        return "lemma-bot"

    monkeypatch.setattr(
        SlackPlatformService, "get_user_display_name", fake_display_name
    )
    surface = _surface(
        surface_type=SurfacePlatform.SLACK,
        surface_identity_email="ident@example.com",
    )

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService(),
        surface_repository=FakeSurfaceRepository(),
    )

    assert reach.email == "ident@example.com"


async def test_live_handle_timeout_falls_back(monkeypatch):
    # A hung provider must not stall the read — it times out and degrades to the
    # account fallback (and persists nothing).
    import asyncio

    monkeypatch.setattr(
        surface_reach_resolver, "_LIVE_HANDLE_TIMEOUT_SECONDS", 0.01
    )

    async def slow_name(self, user_id):
        await asyncio.sleep(0.5)
        return "too-slow"

    monkeypatch.setattr(SlackPlatformService, "get_user_display_name", slow_name)
    surface = _surface(surface_type=SurfacePlatform.SLACK)
    repo = FakeSurfaceRepository()

    reach = await SurfaceReachResolver().resolve(
        surface,
        credential_resolver=FakeCredentialResolver(),
        connector_service=FakeConnectorService("Fallback Account"),
        surface_repository=repo,
    )

    assert reach.handle == "Fallback Account"
    assert repo.updated == []


async def test_response_schema_includes_surface_identity_email_and_reach():
    response = AgentSurfaceResponse(
        id=uuid4(),
        pod_id=uuid4(),
        platform=SurfacePlatform.SLACK,
        name="slack",
        surface_identity_email="bot@example.com",
        reach=SurfaceReach(handle="lemma-bot", email="bot@example.com"),
        config={},
    )

    assert response.surface_identity_email == "bot@example.com"
    assert response.reach is not None
    assert response.reach.handle == "lemma-bot"
    assert response.reach.email == "bot@example.com"
