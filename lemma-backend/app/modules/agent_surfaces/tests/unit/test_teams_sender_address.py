"""Which Teams address may name a Lemma user.

The address a Teams sender resolves by becomes the identity an agent run
executes as. Entra's ``mail`` is set by an administrator, not proven; a user
principal name's suffix is a domain the tenant verified.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.teams.adapter import (
    tenant_verified_address,
)

pytestmark = pytest.mark.unit


def test_a_member_principal_name_is_their_address():
    assert tenant_verified_address("alice@contoso.com") == "alice@contoso.com"


def test_a_guest_principal_name_proves_nothing_about_the_foreign_address():
    assert (
        tenant_verified_address("ceo_victim.com#EXT#@contoso.onmicrosoft.com") is None
    )


@pytest.mark.parametrize("value", [None, "", "not-an-address"])
def test_no_principal_name_is_no_address(value):
    assert tenant_verified_address(value) is None


async def test_the_rosters_email_is_never_the_senders_address(monkeypatch):
    """The Bot Framework roster's ``email`` is Entra's ``mail`` again."""
    from aiohttp import web

    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.platforms.teams.adapter import (
        TeamsSurfaceAdapter,
    )

    async def member(request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "id": "29:user",
                "email": "ceo@victim.com",
                "userPrincipalName": "ceo_victim.com#EXT#@contoso.onmicrosoft.com",
            }
        )

    app = web.Application()
    app.router.add_get("/teams/v3/conversations/{cid}/members/{mid}", member)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_service_extra_host", "127.0.0.1"
    )

    class _Adapter(TeamsSurfaceAdapter):
        async def _get_bot_token(self, tenant_id: str | None = None) -> str | None:
            return "bot-token"

    try:
        email = await _Adapter()._fetch_email_from_bf_connector(
            ParsedInboundSurfaceEvent(
                platform="TEAMS",
                conversation_type=ConversationType.EXTERNAL_DM,
                tenant_id="tenant-1",
                external_thread_id="a:dm",
                message_text="hi",
                is_dm=True,
                reply_target={
                    "conversation_id": "a:dm",
                    "service_url": f"http://127.0.0.1:{port}/teams",
                },
            ),
            "29:user",
        )
    finally:
        await runner.cleanup()

    assert email is None
