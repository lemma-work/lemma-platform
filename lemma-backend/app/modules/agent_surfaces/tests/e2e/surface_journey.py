"""One journey, written once, run on every platform.

The matrix files each say the same thing four or five times. `request_approval`
is a thousand lines of "ask, render a native control, tap it, resume, check the
answer came back", with the journey buried in per-platform plumbing: which
settings to patch, which credential blob points at which fake server, how to
spell a tapped button. The journey is identical; only the plumbing differs, and
the plumbing is what each copy gets subtly wrong.

`stage_surface` owns the plumbing. `SurfaceStage` owns the journey:

    stage = await stage_surface(SurfacePlatform.SLACK, ...)
    context = await stage.say("please show the widget", script=...)
    approve = await stage.control("Approve")
    await stage.press(approve, context=context)
    await stage.resume(context, approval_id=TOOL_CALL_ID)
    assert await stage.saw("Done — approved and executed.")

The part that is not just deduplication is `control`. It reads the control out
of the message the *fake platform actually received*, so a test says "press
Approve" rather than restating an `action_id` the renderer chose. That closes
the loop between egress and ingress: an adapter that renames a control, or
changes the token it puts on one, breaks the journey — where today it breaks
only a `"lemma_approval_approve" in rendered` assertion that the ingress half
never consults, because the ingress half has its own hand-written copy.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _ensure_e2e_runtime_profile,
    _seed_external_user,
    _set_user_mobile_number,
    E2E_RUNTIME_MODEL_NAME,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
    teams as teams_payloads,
    telegram as telegram_payloads,
    whatsapp as whatsapp_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    ScriptTurn,
    process_ingress_and_run_scripted,
    resume_latest_scripted_run,
)
from app.modules.agent.infrastructure.models import AgentModel

#: Every chat platform the "every platform gets the full product" promise
#: covers. Email is not one of them: it has no native controls, and its half of
#: each promise is a single composed reply, which the email tests assert
#: directly.
CHAT_PLATFORMS = (
    SurfacePlatform.SLACK,
    SurfacePlatform.TEAMS,
    SurfacePlatform.TELEGRAM,
    SurfacePlatform.WHATSAPP,
)


def _rendered(messages: list[dict[str, Any]]) -> str:
    """Everything the platform received, as one searchable string.

    ``ensure_ascii=False`` is load-bearing: the default escapes an em dash to
    ``\\u2014``, and every agent reply in these tests has one. Searching the
    escaped form for the unescaped needle finds nothing, and the failure reads
    as "the answer never arrived" while the answer is right there in the dump.
    """
    return json.dumps(messages, default=str, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class RenderedControl:
    """A control the agent rendered, as the platform received it.

    ``value`` is whatever the platform carries back when it is tapped — Slack's
    button value, Telegram's stored callback token, WhatsApp's reply id. ``data``
    is Teams' whole submitted-card payload, which has no single value.
    """

    label: str
    value: str = ""
    action: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SurfaceStage:
    """A pod, an agent and a connected surface on one platform, ready to talk to."""

    platform: SurfacePlatform
    db_session: AsyncSession
    client: AsyncClient
    message_store: Any
    pod_id: str
    agent: dict[str, Any]
    surface: dict[str, Any]
    sender_id: str
    _turn: int = 0
    #: The first turn's thread, so later turns continue the conversation rather
    #: than starting a new one. A follow-up that does not thread is a different
    #: conversation, and a question asked in the first is never answered.
    _thread: str = ""

    # ── saying something ──────────────────────────────────────────────────

    async def say(
        self, text: str, *, script: list[ScriptTurn] | None = None
    ) -> SurfaceChatContext:
        """Deliver one inbound message and drive the run it starts."""
        self._turn += 1
        context = await process_ingress_and_run_scripted(
            self.db_session,
            SurfacePlatformWebhookIngress(
                source=self.platform.value.lower(),
                payload=self._inbound(text),
                headers={},
            ),
            script=script,
        )
        assert isinstance(context, SurfaceChatContext), (
            f"{self.platform.value}: the message did not become a conversation"
        )
        return context

    def _inbound(self, text: str) -> dict[str, Any]:
        turn = self._turn
        if self.platform is SurfacePlatform.SLACK:
            ts = f"17000001{turn:02d}.600600"
            if not self._thread:
                self._thread = ts
            return slack_payloads.dm(
                text=text, ts=ts, thread_ts=None if turn == 1 else self._thread
            )
        if self.platform is SurfacePlatform.TEAMS:
            # A channel mention, not a DM: the staged Teams surface is bound to
            # the captured channel, which is the arrangement a company actually
            # installs and the one the capture came from.
            activity_id = f"teams-journey-{turn}"
            if not self._thread:
                self._thread = activity_id
            return teams_payloads.channel_mention(
                service_url=self.surface["_service_url"],
                text=text,
                activity_id=activity_id,
                reply_to_id=None if turn == 1 else self._thread,
            )
        if self.platform is SurfacePlatform.TELEGRAM:
            return telegram_payloads.dm(
                text=text, message_id=900 + turn, sender_id=int(self.sender_id)
            )
        return whatsapp_payloads.text(
            body=text,
            message_id=f"wamid-journey-{turn:03d}",
            sender_phone=self.sender_id,
        )

    # ── what the person saw ───────────────────────────────────────────────

    async def delivered(self, *, min_count: int = 1) -> list[dict[str, Any]]:
        return await wait_for_messages(
            self.message_store, self.platform.value, min_count=min_count
        )

    async def saw(self, needle: str, *, timeout_seconds: float = 30.0) -> bool:
        """Whether ``needle`` reached the platform, by whichever transport.

        Waits for the *text*, not for a message count. Waiting on a count is
        the mistake that makes these tests look flaky: the prompt has already
        arrived, so a wait for "at least one message" returns at once and the
        assertion runs before the answer the test is about has been delivered.
        """
        if self.platform is SurfacePlatform.SLACK:
            texts = await wait_for_slack_text(
                self.message_store, needle, timeout_seconds=timeout_seconds
            )
            return any(needle in text for text in texts)

        async def probe() -> str:
            return _rendered(self.message_store.get_all(self.platform.value))

        rendered = await eventually(
            label=f"{needle!r} on {self.platform.value}",
            probe=probe,
            done=lambda text: needle in text,
            timeout_seconds=timeout_seconds,
            interval_seconds=0.15,
        )
        return needle in rendered

    # ── pressing what was rendered ────────────────────────────────────────

    async def control(self, label: str) -> RenderedControl:
        """The control the agent rendered under ``label``, ready to press."""
        messages = await self.delivered()
        found = _CONTROL_READERS[self.platform](messages, label)
        assert found is not None, (
            f"{self.platform.value}: nothing rendered a control labelled "
            f"{label!r}. Delivered: {_rendered(messages)[:2000]}"
        )
        return found

    async def press(
        self, control: RenderedControl, *, context: SurfaceChatContext
    ) -> None:
        """Submit the tap, through the real interaction path."""
        del context  # Kept in the signature: a tap always belongs to a turn.
        payload = _PRESS_BUILDERS[self.platform](self, control)
        uow = SqlAlchemyUnitOfWork(self.db_session)
        handled = await build_surface_event_handler(uow).try_handle_interaction(
            SurfacePlatformWebhookIngress(
                source=self.platform.value.lower(), payload=payload, headers={}
            )
        )
        assert handled is True, (
            f"{self.platform.value}: the interaction path refused a control it "
            "had just rendered"
        )
        await uow.commit()

    async def resume(
        self, context: SurfaceChatContext, *, approval_id: str | None = None
    ) -> None:
        await resume_latest_scripted_run(
            self.db_session,
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            pod_id=context.pod_id,
            agent_name=context.agent_name,
            approval_id=approval_id,
        )


# ── reading a rendered control back out of what the platform received ─────


def _slack_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Slack params are recorded through ``str()``; decode blocks back."""
    raw = message.get("blocks")
    if isinstance(raw, list):
        return [block for block in raw if isinstance(block, dict)]
    if not raw:
        return []
    try:
        decoded = ast.literal_eval(str(raw))
    except ValueError, SyntaxError:
        return []
    return (
        [block for block in decoded if isinstance(block, dict)]
        if isinstance(decoded, list)
        else []
    )


def _slack_control(
    messages: list[dict[str, Any]], label: str
) -> RenderedControl | None:
    for message in reversed(messages):
        for block in _slack_blocks(message):
            for element in block.get("elements") or []:
                if not isinstance(element, dict):
                    continue
                text = (element.get("text") or {}).get("text")
                if text == label and element.get("action_id"):
                    return RenderedControl(
                        label=label,
                        value=str(element.get("value") or ""),
                        action=str(element["action_id"]),
                    )
    return None


def _teams_control(
    messages: list[dict[str, Any]], label: str
) -> RenderedControl | None:
    for message in reversed(messages):
        for attachment in (message.get("body") or {}).get("attachments") or []:
            content = (attachment or {}).get("content") or {}
            for action in content.get("actions") or []:
                if isinstance(action, dict) and action.get("title") == label:
                    return RenderedControl(
                        label=label, data=dict(action.get("data") or {})
                    )
    return None


def _telegram_control(
    messages: list[dict[str, Any]], label: str
) -> RenderedControl | None:
    for message in reversed(messages):
        markup = message.get("reply_markup") or {}
        for row in markup.get("inline_keyboard") or []:
            for button in row or []:
                if isinstance(button, dict) and button.get("text") == label:
                    return RenderedControl(
                        label=label, value=str(button.get("callback_data") or "")
                    )
    return None


def _whatsapp_control(
    messages: list[dict[str, Any]], label: str
) -> RenderedControl | None:
    for message in reversed(messages):
        interactive = message.get("interactive") or {}
        for button in (interactive.get("action") or {}).get("buttons") or []:
            reply = (button or {}).get("reply") or {}
            if reply.get("title") == label:
                return RenderedControl(label=label, value=str(reply.get("id") or ""))
    return None


_CONTROL_READERS = {
    SurfacePlatform.SLACK: _slack_control,
    SurfacePlatform.TEAMS: _teams_control,
    SurfacePlatform.TELEGRAM: _telegram_control,
    SurfacePlatform.WHATSAPP: _whatsapp_control,
}


_PRESS_BUILDERS = {
    SurfacePlatform.SLACK: lambda stage, control: slack_payloads.button_press(
        value=control.value, action_id=control.action, sender=stage.sender_id
    ),
    SurfacePlatform.TEAMS: lambda stage, control: teams_payloads.card_submit(
        service_url=stage.surface["_service_url"], values=control.data
    ),
    SurfacePlatform.TELEGRAM: lambda stage, control: telegram_payloads.button_press(
        token=control.value, sender_id=int(stage.sender_id)
    ),
    SurfacePlatform.WHATSAPP: lambda stage, control: whatsapp_payloads.button_press(
        reply_id=control.value, title=control.label
    ),
}


# ── the plumbing each platform needs before it can be talked to ───────────


async def _make_approved_tool_resolvable(
    db_session: AsyncSession, *, agent_id: str, organization_id: str
) -> None:
    """Point the agent's OWN ``agent_runtime`` at the fake e2e runtime profile.

    ``request_approval``'s wrapped-tool execution runs independently of the
    scripted harness run and resolves the AGENT's runtime profile, not the
    paused RUN's — so an approval journey that skips this approves a tool the
    executor then declines to run, and the test reads as a product failure.
    """
    runtime_profile_id = await _ensure_e2e_runtime_profile(
        db_session, organization_id=UUID(organization_id)
    )
    agent = await db_session.get(AgentModel, UUID(agent_id))
    assert agent is not None
    agent.agent_runtime = {
        "profile_id": runtime_profile_id,
        "model_name": E2E_RUNTIME_MODEL_NAME,
    }
    await db_session.commit()


async def stage_surface(
    platform: SurfacePlatform,
    *,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod: dict[str, Any],
    fixed_test_user: dict[str, Any],
    fixed_test_org: dict[str, Any],
    message_store: Any,
    monkeypatch: Any,
    fake: Any,
    toolsets: list[str] | None = None,
) -> SurfaceStage:
    """Everything one platform needs before a journey can start on it.

    ``fake`` is that platform's fake server fixture. The settings patched here
    are the ones the platform's outbound client reads; each was copied from the
    matrix test that owned it, which is the point — they were five copies.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    # What a link card points at. Anything the agent cannot send natively
    # degrades to a link, so this is staging rather than one test's business.
    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    pod_id = test_pod["id"]
    config: dict[str, Any] = {"type": platform.value}
    sender_id = ""
    extras: dict[str, Any] = {}

    if platform is SurfacePlatform.SLACK:
        monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
        account = await _ensure_connector_account(
            db_session,
            user_id=fixed_test_user["id"],
            connector_id="slack",
            credentials={
                "access_token": "xoxb-journey",
                "scope": "chat:write",
                "api_base_url": fake.base_url,
                "raw_response": {
                    "bot_user_id": slack_payloads.BOT_USER_ID,
                    "team_id": slack_payloads.TEAM_ID,
                    "api_base_url": fake.base_url,
                },
            },
        )
        config["account_id"] = str(account.id)
        sender_id = slack_payloads.SENDER_ID
        # Where a reply is expected to land, so a test asserting the
        # destination does not restate the capture's channel id.
        extras["_dm_channel"] = slack_payloads.DM_CHANNEL_ID
    elif platform is SurfacePlatform.TEAMS:
        from app.modules.agent_surfaces.platforms.teams.adapter import (
            TeamsSurfaceAdapter,
        )

        async def _bot_token(self: Any, tenant_id: str) -> str | None:
            del self, tenant_id
            return "teams-bot-token"

        async def _no_graph(self: Any, tenant_id: str) -> str | None:
            del self, tenant_id
            return None

        # The Bot Framework token and the Graph lookup are the two calls that
        # leave the process before anything is rendered; the fake serves the
        # rest.
        monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _bot_token)
        monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _no_graph)
        monkeypatch.setattr(
            surface_settings,
            "microsoft_bot_openid_config_url",
            fake.openid_config_url,
        )
        monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
        account = await _ensure_connector_account(
            db_session,
            user_id=fixed_test_user["id"],
            connector_id="microsoft_teams",
            credentials={
                "access_token": "teams-token",
                "user_data": {"tenant_id": teams_payloads.TENANT_ID},
            },
        )
        config["account_id"] = str(account.id)
        config["allowed_channel_ids"] = [teams_payloads.CHANNEL_ID]
        sender_id = teams_payloads.SENDER_AAD_ID
        extras["_service_url"] = fake.service_url
    elif platform is SurfacePlatform.TELEGRAM:
        monkeypatch.setattr(surface_settings, "telegram_bot_token", "journey-telegram")
        monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "journey")
        monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
        monkeypatch.setattr(
            "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
            f"{fake.api_base}/bot",
        )
        sender_id = str(telegram_payloads.SENDER_ID)
    else:
        monkeypatch.setattr(
            "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
            f"{fake.api_base}/v21.0",
        )
        monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
        monkeypatch.setattr(
            surface_settings,
            "whatsapp_phone_number_id",
            whatsapp_payloads.PHONE_NUMBER_ID,
        )
        monkeypatch.setattr(
            surface_settings, "whatsapp_waba_id", whatsapp_payloads.WABA_ID
        )
        monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
        sender_id = whatsapp_payloads.SENDER_PHONE

    agent, surface = await _create_agent_surface(
        authenticated_client, pod_id, config=config, toolsets=toolsets
    )
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )

    # How each platform's sender becomes the test's Lemma user. Slack and Teams
    # resolve through the fetched profile's email, so nothing is seeded; the
    # shared-bot platforms resolve by id or by number.
    if platform is SurfacePlatform.TELEGRAM:
        await _seed_external_user(
            db_session,
            platform="TELEGRAM",
            external_user_id=sender_id,
            resolved_user_id=UUID(fixed_test_user["id"]),
        )
    elif platform is SurfacePlatform.WHATSAPP:
        await _set_user_mobile_number(
            db_session, user_id=fixed_test_user["id"], mobile_number=sender_id
        )

    return SurfaceStage(
        platform=platform,
        db_session=db_session,
        client=authenticated_client,
        message_store=message_store,
        pod_id=pod_id,
        agent=agent,
        surface={**surface, **extras},
        sender_id=sender_id,
    )
