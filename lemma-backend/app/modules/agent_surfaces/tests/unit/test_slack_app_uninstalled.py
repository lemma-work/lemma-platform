"""Slack removing the app: how it is read, verified, and said in setup.

What it does to the database -- the account retired, the groups archived, and
only the app that signed it -- is in ``e2e/test_slack_app_removal_e2e.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.api.controllers.slack_webhook_verification import (
    slack_candidates_for_workspace,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.setup_actions import (
    build_surface_setup_actions,
)
from app.modules.agent_surfaces.domain.surface_representation import (
    SurfaceConnection,
    SurfaceConnectionOwner,
    SurfaceConnectionStatus,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.slack.uninstall import is_uninstall_delivery
from app.modules.test_support.surface_routing_double import routing_surfaces_double

pytestmark = pytest.mark.asyncio

_REPAIR = "https://app.example.test/t/pod?settings=connectors"


def _callback(event: dict) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "authorizations": [{"user_id": "U_INSTALLER", "is_bot": False}],
        "event": event,
    }


async def test_app_uninstalled_is_an_uninstall_of_every_bot():
    parsed = SlackMessageParser().parse_lifecycle(
        _callback({"type": "app_uninstalled"})
    )

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.UNINSTALLED
    assert parsed.tenant_id == "T1"
    assert parsed.revoked_identity_ids is None
    assert parsed.actor_external_user_id is None


async def test_a_revoked_bot_token_names_the_bot():
    parsed = SlackMessageParser().parse_lifecycle(
        _callback(
            {
                "type": "tokens_revoked",
                "tokens": {"oauth": ["U_PERSON"], "bot": ["U_BOT"]},
            }
        )
    )

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.UNINSTALLED
    assert parsed.revoked_identity_ids == ["U_BOT"]


async def test_revoking_only_a_persons_token_leaves_the_bot_answering():
    """The bot still has its token; a dead user token is the connector's to find."""
    parsed = SlackMessageParser().parse_lifecycle(
        _callback({"type": "tokens_revoked", "tokens": {"oauth": ["U_PERSON"]}})
    )

    assert parsed is None


@pytest.mark.parametrize(
    "event",
    [
        {"type": ["app_uninstalled"]},
        {"type": {"app": "uninstalled"}},
        {"type": None},
        "app_uninstalled",
    ],
)
async def test_an_unhashable_or_missing_event_type_is_not_an_uninstall(event):
    """Read before the signature is checked, so the body is anybody's: a type
    that is not a string is no uninstall, not a 500."""
    assert is_uninstall_delivery({"type": "event_callback", "event": event}) is False


def _surface(identity: str | None, *, status: str = "ACTIVE"):
    return SimpleNamespace(
        id=uuid4(),
        account_id=uuid4(),
        surface_identity_id=identity,
        surface_type=SurfacePlatform.SLACK,
        external_workspace_id="T1",
        status=status,
    )


async def _candidate_receivers(payload: dict, surfaces) -> set:
    class _Resolver:
        async def slack_webhook_credentials(self, surface):
            return SimpleNamespace(app_id="A1", signing_secret="secret")

    service = SimpleNamespace(
        credential_resolver=_Resolver(),
        surface_repository=SimpleNamespace(
            list_active_for_routing=routing_surfaces_double(surfaces)
        ),
    )
    candidates = await slack_candidates_for_workspace(
        service=service,
        team_id="T1",
        also_paused=is_uninstall_delivery(payload),
    )
    return {surface_id for c in candidates for surface_id in c.receiver_surface_ids}


@pytest.mark.parametrize(
    "status", ["INACTIVE", "NEEDS_SETUP", "ERROR", "PENDING_ADMIN_CONSENT"]
)
async def test_a_surface_in_any_status_verifies_its_apps_removal_and_nothing_else(
    status,
):
    """The signature check is where a quiet surface's uninstall used to stop.
    Paused or not, waiting on setup or not, it is coming back one day."""
    live, quiet = _surface("U_BOT_A"), _surface("U_BOT_B", status=status)

    removal = await _candidate_receivers(
        _callback({"type": "app_uninstalled"}), [live, quiet]
    )
    message = await _candidate_receivers(
        _callback({"type": "message", "text": "hi"}), [live, quiet]
    )

    assert removal == {live.id, quiet.id}
    assert message == {live.id}


def _retired(*, is_you: bool, is_pod_member: bool = True) -> SurfaceConnection:
    return SurfaceConnection(
        account_id=uuid4(),
        connector_id="slack",
        status=SurfaceConnectionStatus.REAUTH_REQUIRED,
        connected_by=SurfaceConnectionOwner(
            user_id=uuid4(),
            name="Priya",
            is_pod_member=is_pod_member,
            is_you=is_you,
        ),
    )


def _reinstall(connection: SurfaceConnection):
    actions = build_surface_setup_actions(
        platform=SurfacePlatform.SLACK,
        is_custom_app=False,
        webhook_url=None,
        slack_retired_connection=connection,
        slack_repair_url=_REPAIR,
    )
    assert [action.key for action in actions] == ["slack_reinstall"]
    [action] = actions
    assert action.is_blocking
    assert action.link == _REPAIR
    return action


async def test_setup_asks_for_a_reinstall_even_on_lemmas_own_app():
    """System apps usually need nothing, but an uninstall takes Lemma's with it.
    The person who connected it is told to reconnect it."""
    action = _reinstall(_retired(is_you=True))

    assert action.link_label == "Reconnect Slack"
    assert "your Slack connection" in action.description
    assert "Priya" not in action.description


async def test_a_teammate_is_told_who_can_reconnect_it():
    """Reconnecting repairs the owner's account only: a teammate connecting
    Slack makes one of their own, and the step would never clear."""
    action = _reinstall(_retired(is_you=False))

    assert "Priya's Slack connection" in action.description
    assert action.steps[0] == "Ask Priya to reconnect Slack from their Connectors."
    assert any("switch this surface" in step for step in action.steps)


async def test_with_its_owner_gone_the_only_way_back_is_your_own_connection():
    action = _reinstall(_retired(is_you=False, is_pod_member=False))

    assert "no longer in this pod" in action.description
    assert not any(step.startswith("Ask") for step in action.steps)
    assert action.steps[1] == "Switch this surface to your account."
