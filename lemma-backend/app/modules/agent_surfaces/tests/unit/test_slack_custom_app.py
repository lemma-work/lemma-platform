from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.common import computed_webhook_url
from app.modules.agent_surfaces.services.webhook_security_service import (
    SlackWebhookVerificationCandidate,
    SurfaceWebhookSecurityService,
)

pytestmark = pytest.mark.asyncio

OWN_SECRET = "the-orgs-own-signing-secret"
DEPLOYMENT_SECRET = "lemmas-own-signing-secret"


def _surface(*, webhook_secret: str | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type="SLACK",
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
        webhook_secret=webhook_secret,
    )


def _signed(secret: str, body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return {"x-slack-request-timestamp": ts, "x-slack-signature": f"v0={digest}"}


async def test_a_workspaces_own_slack_app_verifies_against_its_own_secret(monkeypatch):
    """Bring-your-own Slack app signs with a secret the deployment never sees.

    Without this the shared platform check runs, the deployment's secret fails
    the HMAC, and every event from that app is rejected — which is why this
    looked architecturally impossible rather than simply unfinished.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    await SurfaceWebhookSecurityService().verify_surface_request(
        surface=_surface(webhook_secret=OWN_SECRET),
        headers=_signed(OWN_SECRET, body),
        raw_body=body,
    )


async def test_the_deployments_secret_cannot_sign_for_a_custom_app(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    with pytest.raises(Exception):
        await SurfaceWebhookSecurityService().verify_surface_request(
            surface=_surface(webhook_secret=OWN_SECRET),
            headers=_signed(DEPLOYMENT_SECRET, body),
            raw_body=body,
        )


async def test_a_surface_without_its_own_secret_still_uses_the_deployments(monkeypatch):
    """The shared Lemma app must keep working exactly as before."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback"}'

    await SurfaceWebhookSecurityService().verify_surface_request(
        surface=_surface(webhook_secret=None),
        headers=_signed(DEPLOYMENT_SECRET, body),
        raw_body=body,
    )


async def test_a_custom_app_delivers_to_the_same_shared_endpoint(monkeypatch):
    """Running your own Slack app does not change where events arrive.

    The secret that verifies a request is chosen from the workspace in the
    payload, not from the URL it came in on, so one endpoint serves every app.
    A per-surface URL would make the manifest depend on a surface existing —
    which is backwards, since you need the app before you can create one.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.common.public_https_api_url_available",
        lambda: True,
    )

    shared_url = "https://api.example.test/surfaces/webhooks/slack"
    assert computed_webhook_url(_surface(webhook_secret=OWN_SECRET)) == shared_url
    assert computed_webhook_url(_surface(webhook_secret=None)) == shared_url


async def test_the_manifest_needs_no_surface(monkeypatch):
    """Both URLs the manifest carries are deployment-wide.

    You paste the manifest to create the app, whose client id creates the
    account, on which the surface is built. Anything surface-scoped could only
    be read after the point where it was needed.
    """
    from app.core.config import settings
    from app.modules.agent_surfaces.platforms.slack.manifest import (
        build_slack_app_manifest,
    )

    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.common.public_https_api_url_available",
        lambda: True,
    )

    manifest = build_slack_app_manifest()

    expected = "https://api.example.test/surfaces/webhooks/slack"
    assert manifest["settings"]["event_subscriptions"]["request_url"] == expected
    assert manifest["settings"]["interactivity"]["request_url"] == expected
    assert manifest["oauth_config"]["redirect_urls"] == [
        "https://api.example.test/connectors/connect-requests/oauth/callback"
    ]


async def test_the_manifest_subscribes_to_everything_this_branch_needs():
    """The manifest is the only place bot events are declared — keep it that way.

    The setup checklist used to restate them, and named four where the manifest
    declares six. An app built by following it had no ``app_home_opened``, so
    the App Home tab never opened, and no ``member_joined_channel``, so being
    invited to a channel started nothing. Neither failure looks like a missing
    subscription from the outside; both look like Lemma being broken.
    """
    from app.modules.agent_surfaces.platforms.slack.manifest import (
        build_slack_app_manifest,
    )

    events = set(
        build_slack_app_manifest()["settings"]["event_subscriptions"]["bot_events"]
    )

    assert {
        "app_home_opened",  # the App Home tab
        "member_joined_channel",  # invite-is-setup
        "app_mention",
        "message.im",
        "message.channels",
        "message.groups",
    } <= events


async def test_the_setup_checklist_does_not_restate_the_events():
    """Two lists of the same thing drift, and the copy is the one that loses."""
    from app.modules.agent_surfaces.domain.setup_actions import (
        build_surface_setup_actions,
    )

    actions = build_surface_setup_actions(
        platform=SurfacePlatform.SLACK,
        is_custom_app=True,
        webhook_url="https://api.example.test/surfaces/webhooks/slack",
    )

    prose = " ".join(step for action in actions for step in action.steps)
    assert "app_mention" not in prose
    assert "message.im" not in prose


async def test_the_shared_endpoint_accepts_a_workspaces_own_app(monkeypatch):
    """The whole point of the rework: an org's own app verifies on the shared
    endpoint, against its own secret rather than the deployment's."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback","team_id":"T1"}'

    surface_id = uuid4()
    verified = SurfaceWebhookSecurityService().verify_slack_request(
        headers=_signed(OWN_SECRET, body),
        raw_body=body,
        api_app_id="A_CUSTOM",
        candidates=[
            SlackWebhookVerificationCandidate(
                app_id="A_CUSTOM",
                signing_secret=OWN_SECRET,
                receiver_surface_ids=(surface_id,),
            )
        ],
    )
    assert verified.receiver_surface_ids == (surface_id,)


async def test_one_workspace_may_front_several_pods(monkeypatch):
    """A workspace can carry surfaces in several pods — that is the supported
    multi-pod case. Each stores its own copy of the same app secret, so a
    signature valid for any candidate is valid for the workspace."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback","team_id":"T1"}'

    current_surface = uuid4()
    stale_surface = uuid4()
    verified = SurfaceWebhookSecurityService().verify_slack_request(
        headers=_signed(OWN_SECRET, body),
        raw_body=body,
        api_app_id="A_CUSTOM",
        candidates=[
            SlackWebhookVerificationCandidate(
                app_id="A_CUSTOM",
                signing_secret="a-stale-copy",
                receiver_surface_ids=(stale_surface,),
            ),
            SlackWebhookVerificationCandidate(
                app_id="A_CUSTOM",
                signing_secret=OWN_SECRET,
                receiver_surface_ids=(current_surface,),
            ),
        ],
    )
    assert verified.receiver_surface_ids == (current_surface,)


async def test_a_workspace_with_no_custom_app_still_uses_the_deployments(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback","team_id":"T1"}'

    surface_id = uuid4()
    verified = SurfaceWebhookSecurityService().verify_slack_request(
        headers=_signed(DEPLOYMENT_SECRET, body),
        raw_body=body,
        api_app_id="A_MANAGED",
        candidates=[
            SlackWebhookVerificationCandidate(
                app_id="A_MANAGED",
                signing_secret=DEPLOYMENT_SECRET,
                receiver_surface_ids=(surface_id,),
            )
        ],
    )
    assert verified.receiver_surface_ids == (surface_id,)


async def test_a_signature_matching_no_candidate_is_rejected(monkeypatch):
    """A workspace running its own app must not fall back to the deployment's
    secret — that would let anyone holding the shared secret speak for it."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", DEPLOYMENT_SECRET)
    body = b'{"type":"event_callback","team_id":"T1"}'

    with pytest.raises(Exception):
        SurfaceWebhookSecurityService().verify_slack_request(
            headers=_signed(DEPLOYMENT_SECRET, body),
            raw_body=body,
            api_app_id="A_CUSTOM",
            candidates=[
                SlackWebhookVerificationCandidate(
                    app_id="A_CUSTOM",
                    signing_secret=OWN_SECRET,
                    receiver_surface_ids=(uuid4(),),
                )
            ],
        )


async def test_a_valid_signature_for_one_app_cannot_target_another_app(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    body = b'{"type":"event_callback","team_id":"T1","api_app_id":"A_B"}'

    with pytest.raises(Exception):
        SurfaceWebhookSecurityService().verify_slack_request(
            headers=_signed(OWN_SECRET, body),
            raw_body=body,
            api_app_id="A_B",
            candidates=[
                SlackWebhookVerificationCandidate(
                    app_id="A_A",
                    signing_secret=OWN_SECRET,
                    receiver_surface_ids=(uuid4(),),
                ),
                SlackWebhookVerificationCandidate(
                    app_id="A_B",
                    signing_secret="different-app-secret",
                    receiver_surface_ids=(uuid4(),),
                ),
            ],
        )


async def test_the_manifest_names_the_app_after_the_agent_it_is_for():
    """One Slack app is one bot user, so a bot for one agent is named for it.

    The manifest is the only chance to set that name without a person editing
    it in Slack afterwards, which is exactly the step this is here to remove.
    """
    from app.modules.agent_surfaces.platforms.slack.manifest import (
        build_slack_app_manifest,
    )

    manifest = build_slack_app_manifest(agent_name="Triage")

    assert manifest["display_information"]["name"] == "Triage"
    assert manifest["features"]["bot_user"]["display_name"] == "Triage"
    # The description is written around the name, so leaving it alone would ship
    # a bot called Triage introducing itself as Lemma.
    assert "Lemma" not in manifest["features"]["agent_view"]["agent_description"]
    assert "Triage" in manifest["features"]["agent_view"]["agent_description"]


async def test_the_manifest_still_defaults_to_lemma():
    """Naming an agent is optional; the shared bot is still the common case."""
    from app.modules.agent_surfaces.platforms.slack.manifest import (
        build_slack_app_manifest,
    )

    manifest = build_slack_app_manifest()

    assert manifest["display_information"]["name"] == "Lemma"
    assert manifest["features"]["bot_user"]["display_name"] == "Lemma"


async def test_an_agent_name_slack_would_reject_is_made_acceptable():
    """Slack validates the manifest on paste, and rejects the whole document.

    A period in a bot's display name, or a name past its length cap, does not
    surface as a warning — it surfaces as an error page in front of the person
    who just clicked "make my app", naming none of this.
    """
    from app.modules.agent_surfaces.platforms.slack.manifest import (
        build_slack_app_manifest,
        slack_app_name,
    )

    assert "." not in slack_app_name("release v2.1 bot")
    assert slack_app_name("   ") == "Lemma"
    assert slack_app_name(None) == "Lemma"

    long_name = build_slack_app_manifest(agent_name="a" * 60)
    assert len(long_name["display_information"]["name"]) == 35


async def test_a_custom_app_with_no_recorded_app_id_is_reported_as_unfinished():
    """The other half of "Lemma cannot verify this app's events".

    Inbound narrows candidates by ``(app_id, signing_secret)`` and needs both,
    so an account that never stored an app id is skipped and every event for it
    is rejected. Only the missing secret was reported, which left the other
    failure looking like Lemma being broken — the surface said it was ready and
    then answered nothing.
    """
    from app.modules.agent_surfaces.domain.setup_actions import (
        build_surface_setup_actions,
    )

    actions = build_surface_setup_actions(
        platform=SurfacePlatform.SLACK,
        is_custom_app=True,
        webhook_url="https://api.example.test/surfaces/webhooks/slack",
        slack_app_id_missing=True,
    )

    app_id_action = next(a for a in actions if a.key == "slack_app_id")
    assert app_id_action.is_blocking
