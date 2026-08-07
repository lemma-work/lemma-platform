from __future__ import annotations

import json
import pathlib

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

pytestmark = pytest.mark.asyncio

_DM_SCOPES = "chat:write,assistant:write"


def _event(*, is_dm: bool = True) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id="D1" if is_dm else "C1",
        external_thread_id="100.0",
        external_message_id="100.0",
        message_text="hi",
        is_dm=is_dm,
        reply_target={"channel": "D1" if is_dm else "C1", "thread_ts": "100.0"},
    )


def _api_error(code: str) -> SlackApiError:
    return SlackApiError(message=code, response=dict({"error": code}))


async def test_thread_title_is_set_for_a_dm(monkeypatch):
    calls: list[dict] = []

    async def fake_set_title(self, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "assistant_threads_setTitle", fake_set_title)
    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": _DM_SCOPES}
    )

    assert await svc.set_thread_title(event=_event(), title="Q3 pipeline") is True
    assert calls == [
        {"channel_id": "D1", "thread_ts": "100.0", "title": "Q3 pipeline"}
    ]


async def test_thread_title_is_skipped_outside_a_dm_and_without_scope(monkeypatch):
    """Titles belong to agent threads; a channel has its own name already."""

    async def fake_set_title(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("should not title a channel or an unscoped install")

    monkeypatch.setattr(AsyncWebClient, "assistant_threads_setTitle", fake_set_title)

    scoped = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": _DM_SCOPES}
    )
    assert await scoped.set_thread_title(event=_event(is_dm=False), title="x") is False

    unscoped = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": "chat:write"}
    )
    assert await unscoped.set_thread_title(event=_event(), title="x") is False


async def test_thread_title_never_raises(monkeypatch):
    """A workspace that refuses the call must not break the inbound turn."""

    async def fake_set_title(self, **kwargs):
        raise _api_error("missing_scope")

    monkeypatch.setattr(AsyncWebClient, "assistant_threads_setTitle", fake_set_title)
    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": _DM_SCOPES}
    )

    assert await svc.set_thread_title(event=_event(), title="Q3 pipeline") is False


async def test_suggested_prompts_are_capped_and_shaped(monkeypatch):
    calls: list[dict] = []

    async def fake_set_prompts(self, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        AsyncWebClient, "assistant_threads_setSuggestedPrompts", fake_set_prompts
    )
    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": _DM_SCOPES}
    )

    delivered = await svc.set_suggested_prompts(
        event=_event(),
        prompts=[
            ("Pipeline", "Show me the pipeline"),
            ("Blockers", "What is blocked?"),
            ("Owners", "Who owns what?"),
            ("Risks", "What is at risk?"),
            ("Fifth", "Dropped — Slack takes four"),
            ("", "no title, dropped"),
        ],
        title="Try one of these",
    )

    assert delivered is True
    prompts = calls[0]["prompts"]
    assert [p["title"] for p in prompts] == [
        "Pipeline",
        "Blockers",
        "Owners",
        "Risks",
    ]
    assert calls[0]["title"] == "Try one of these"
    assert calls[0]["channel_id"] == "D1"


async def test_suggested_prompts_need_at_least_one_usable_pair(monkeypatch):
    async def fake_set_prompts(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("should not call Slack with nothing to suggest")

    monkeypatch.setattr(
        AsyncWebClient, "assistant_threads_setSuggestedPrompts", fake_set_prompts
    )
    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": _DM_SCOPES}
    )

    assert await svc.set_suggested_prompts(event=_event(), prompts=[]) is False
    assert (
        await svc.set_suggested_prompts(event=_event(), prompts=[("  ", "  ")]) is False
    )


async def test_manifest_declares_the_agent_messaging_experience():
    """The app must present as an agent, or none of the above is reachable.

    Guards the two things that silently disable everything: dropping
    ``agent_view`` (Slack stops treating Lemma as an agent) and dropping
    ``assistant:write`` (status, titles and prompts all 403).
    """
    manifest = json.loads(
        (
            pathlib.Path(__file__).parents[5] / "manifests" / "slack" / "manifest.json"
        ).read_text()
    )
    agent_view = manifest["features"]["agent_view"]
    assert 0 < len(agent_view["agent_description"]) <= 300
    assert "assistant:write" in manifest["oauth_config"]["scopes"]["bot"]
    # The legacy view is one-way; having both is a misconfiguration.
    assert "assistant_view" not in manifest["features"]


async def test_agent_avatar_rides_along_with_the_name():
    """A personal name beside the app's generic icon reads as two senders."""
    from app.modules.agent_surfaces.platforms.slack.client import (
        slack_customized_message_kwargs,
    )

    creds = {"access_token": "xoxb", "scope": "chat:write.customize"}
    assert slack_customized_message_kwargs(creds, "agent3", "https://x.test/a.png") == {
        "username": "agent3",
        "icon_url": "https://x.test/a.png",
    }
    # Slack only fetches https icons; anything else would fail the whole send.
    assert slack_customized_message_kwargs(creds, "agent3", "/local/a.png") == {
        "username": "agent3"
    }
    assert slack_customized_message_kwargs(creds, "agent3", None) == {
        "username": "agent3"
    }
    # Without the scope, nothing is customised at all.
    assert slack_customized_message_kwargs({"access_token": "x"}, "a", "https://y") == {}


async def test_setup_confirmation_names_what_was_saved():
    from app.modules.agent_surfaces.platforms.slack.blocks import (
        channel_setup_confirmation_blocks,
    )

    blocks = channel_setup_confirmation_blocks(
        channel_name="sales", agent_label="the pod assistant"
    )
    text = blocks[0]["text"]
    assert "the pod assistant" in text
    assert "#sales" in text


async def test_home_leads_with_value_then_configuration():
    """A first-time viewer should meet the pitch, not the routing table."""
    from app.modules.agent_surfaces.platforms.slack.blocks import app_home_view

    view = app_home_view(
        pod_name="Test1",
        dm_agent_name="agent3",
        channel_routes=[("C1", None)],
        agents=[("agent3", "Answers ops questions")],
        apps=[("Dashboard", "https://d.test")],
        workspace_url="https://lemma.test",
        logo_url="https://x.test/logo.png",
    )
    kinds = [b["type"] for b in view["blocks"]]
    rendered = str(view)

    # Masthead first, settings last.
    assert kinds[0] == "context"  # logo
    assert kinds[1] == "header"
    assert rendered.index("Try one") < rendered.index("Your direct messages")
    # Agents and apps are cards, not stacked paragraphs.
    assert kinds.count("card") == 2
    assert "lemma_agent_dm" in rendered


async def test_home_skips_a_logo_slack_cannot_fetch():
    """Slack loads the image from its own servers; localhost renders an empty box."""
    from app.modules.agent_surfaces.platforms.slack.blocks import app_home_view

    view = app_home_view(
        pod_name=None,
        dm_agent_name=None,
        channel_routes=[],
        logo_url="http://localhost:3710/logo.png",
    )
    assert view["blocks"][0]["type"] == "header"


async def test_publish_home_view_accepts_everything_the_caller_sends():
    """Regression: a signature mismatch here is invisible.

    ``_publish_home`` passed ``logo_url`` that neither the adapter nor the
    service accepted. The TypeError was swallowed by a broad except, nothing
    was published, and Slack kept showing the *previous* Home tab — which is
    indistinguishable from a deploy that never happened.
    """
    import inspect

    from app.modules.agent_surfaces.platforms.slack.adapter import SlackSurfaceAdapter
    from app.modules.agent_surfaces.platforms.slack.blocks import app_home_view

    view_params = set(inspect.signature(app_home_view).parameters) - {"self"}
    adapter_params = set(
        inspect.signature(SlackSurfaceAdapter.publish_home_view).parameters
    )
    service_params = set(
        inspect.signature(SlackPlatformService.publish_home_view).parameters
    )

    # Everything the view can render must be reachable through both layers.
    missing_on_adapter = view_params - adapter_params
    missing_on_service = view_params - service_params
    assert not missing_on_adapter, f"adapter cannot pass: {missing_on_adapter}"
    assert not missing_on_service, f"service cannot pass: {missing_on_service}"
