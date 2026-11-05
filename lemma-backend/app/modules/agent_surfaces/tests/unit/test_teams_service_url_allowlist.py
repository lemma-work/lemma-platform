"""The bot token only goes to a Bot Framework ``serviceUrl``.

Every Teams reply posts Lemma's deployment-wide bot token to the ``serviceUrl``
an inbound activity named. A host outside Bot Framework must be refused before
the token leaves the process.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.teams.attachment_urls import (
    is_bot_framework_service_url,
)

pytestmark = pytest.mark.asyncio


class _RecordingTeamsAdapter(TeamsSurfaceAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.token_requests = 0
        self.posted_to: list[str] = []

    async def _get_bot_token(self, tenant_id: str | None = None) -> str | None:
        del tenant_id
        self.token_requests += 1
        return "bot-token"

    async def _post_activity(self, url, *, token, body):
        del token, body
        self.posted_to.append(url)


def _event(service_url: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="tenant-1",
        external_channel_id="a:dm",
        external_thread_id="a:dm",
        message_text="hi",
        is_dm=True,
        reply_target={"conversation_id": "a:dm", "service_url": service_url},
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://smba.trafficmanager.net/amer/",
        "https://smba.trafficmanager.net/teams",
        "https://europe.webchat.botframework.com/",
        "https://smba.infra.gcc.teams.microsoft.com/",
        "https://smba.infra.gov.teams.microsoft.us/",
        "https://smba.infra.dod.teams.microsoft.us/",
    ],
)
async def test_bot_framework_reply_hosts_are_allowed(url: str) -> None:
    assert is_bot_framework_service_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        # Anyone can register a Traffic Manager profile under this domain.
        "https://attacker.trafficmanager.net/",
        "https://smba.trafficmanager.net.evil.example/",
        "https://botframework.com.evil.example/",
        "http://smba.trafficmanager.net/amer/",
        "",
    ],
)
async def test_other_hosts_are_refused(url: str) -> None:
    assert not is_bot_framework_service_url(url)


async def test_a_configured_extra_host_is_allowed_in_addition(monkeypatch) -> None:
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_service_extra_host", "127.0.0.1"
    )

    assert client.bf_service_url("http://127.0.0.1:9/teams/") == (
        "http://127.0.0.1:9/teams"
    )
    assert client.bf_service_url(None) == "https://smba.trafficmanager.net/teams"


async def test_a_reply_to_a_foreign_service_url_is_refused_before_posting() -> None:
    adapter = _RecordingTeamsAdapter()

    with pytest.raises(AgentSurfaceValidationError, match="Bot Framework"):
        await adapter.send_message(
            credentials={}, event=_event("https://evil.example/"), message="hello"
        )

    assert adapter.posted_to == []


async def test_a_reply_to_bot_framework_is_posted() -> None:
    adapter = _RecordingTeamsAdapter()

    await adapter.send_message(
        credentials={},
        event=_event("https://smba.trafficmanager.net/amer/"),
        message="hello",
    )

    assert adapter.posted_to == [
        "https://smba.trafficmanager.net/amer/v3/conversations/a%3Adm/activities"
    ]


async def test_a_member_lookup_on_a_foreign_service_url_never_fetches_a_token() -> None:
    adapter = _RecordingTeamsAdapter()

    email = await adapter._fetch_email_from_bf_connector(
        _event("https://evil.example/"), "29:user"
    )

    assert email is None
    assert adapter.token_requests == 0
