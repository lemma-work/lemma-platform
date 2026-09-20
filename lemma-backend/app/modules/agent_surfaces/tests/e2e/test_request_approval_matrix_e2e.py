"""`request_approval` on every chat platform, and its absence on email.

The promise is PS-SURF-021: an approval is presented natively where the
platform has buttons and as readable text where it does not, the person may
answer either way, and it is never dropped for want of native support.

This was four near-identical tests plus two email ones. The four are now one
journey run per platform, because the differences between them were plumbing
rather than product — and the plumbing hid the one thing worth checking, which
is that the control the agent *rendered* is the control the ingress path can
read back. `stage.control("Approve")` finds the button in what the platform
actually received and presses that; nothing here spells an `action_id`.

Email keeps its own tests. It has no native control to press, and its half of
the promise is the opposite shape: the approval travels inside the single
composed reply, and the person answers by replying.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
import json

from app.modules.agent_surfaces.events.handlers import (
    build_surface_ingress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from uuid import UUID

from sqlalchemy import text

from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _resend_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    suppress_agent_run_enqueue,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_request_approval,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    SurfaceStage,
    _make_approved_tool_resolvable,
    stage_surface,
)

pytestmark = pytest.mark.e2e

TOOL_CALL_ID = "call_approval_matrix_1"
_INNER_TOOL_ARGS = {
    "type": "WIDGET",
    "content": "<div class='status'><span>Ready</span></div>",
}


def approval_script(final_text: str) -> list:
    return [
        script_request_approval(
            tool_name="display_resource",
            args={"request": _INNER_TOOL_ARGS},
            title="Show a widget",
            reason="Needs your OK first",
            tool_call_id=TOOL_CALL_ID,
        ),
        script_text(final_text),
    ]


async def run_deferred_reconciliation(
    db_session: AsyncSession, *, conversation_id, pod_id
) -> None:
    """Do the half a platform webhook defers to the worker.

    Surface interactions call ``resolve_user_approval_internal`` with
    ``defer_reconciliation=True``: the decision commits inline, but running the
    approved tool and starting the resume run are handed to the
    ``reconcile_agent_approval`` job so the webhook can answer immediately.
    These tests drive the ingress directly and run no worker, so without this
    no resume run is ever created. A denial needs nothing here — only an
    approved tool defers.
    """
    from contextlib import asynccontextmanager

    from app.modules.agent.events.handlers import reconcile_agent_approval_now

    @asynccontextmanager
    async def uow_factory():
        yield SqlAlchemyUnitOfWork(db_session)

    await reconcile_agent_approval_now(
        {
            "conversation_id": str(conversation_id),
            "approval_id": TOOL_CALL_ID,
            "pod_id": str(pod_id),
        },
        uow_factory=uow_factory,
    )


async def _tool_return(client: AsyncClient, *, pod_id: str, conversation_id) -> dict:
    messages = await _messages_for_conversation(
        client, pod_id=pod_id, conversation_id=str(conversation_id)
    )
    return next(
        message["tool_result"]
        for message in messages
        if message.get("tool_call_id") == TOOL_CALL_ID
        and message.get("kind") == "TOOL_RETURN"
    )


async def _staged(platform: SurfacePlatform, platform_fake, **kwargs) -> SurfaceStage:
    return await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        **kwargs,
    )


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_a_tapped_approve_runs_the_tool_and_resumes_the_run(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await _staged(
        platform,
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say(
        "please show the widget",
        script=approval_script("Done — approved and executed."),
    )

    assert await stage.saw("Show a widget"), "the request never reached the person"
    approve = await stage.control("Approve")
    await stage.press(approve)

    await run_deferred_reconciliation(
        db_session, conversation_id=context.conversation_id, pod_id=context.pod_id
    )
    await stage.resume(context, approval_id=TOOL_CALL_ID)

    assert await stage.saw("Done — approved and executed."), (
        "the resumed run never reached the platform"
    )
    result = await _tool_return(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=context.conversation_id,
    )
    assert result["decision"] == "APPROVE_ONCE"
    assert result["executed"] is True


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_a_tapped_deny_skips_the_wrapped_tool(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await _staged(
        platform,
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say(
        "please show the widget",
        script=approval_script("Understood — I did not run it."),
    )

    deny = await stage.control("Deny")
    await stage.press(deny)
    # No reconciliation: only an approved tool defers its execution to a job.
    await stage.resume(context, approval_id=TOOL_CALL_ID)

    result = await _tool_return(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=context.conversation_id,
    )
    assert result["decision"] == "DENY"
    assert result["executed"] is False


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_a_typed_deny_is_accepted_like_the_button(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    """The promise says either way, and this half was only tested on Slack.

    Nothing is pressed: the person answers the question by replying to it, the
    way they would if their client had not rendered the buttons at all.
    """
    stage = await _staged(
        platform,
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say(
        "please show the widget",
        script=approval_script("Understood — I did not run it."),
    )
    assert await stage.saw("Show a widget")

    await stage.say("deny")

    result = await _tool_return(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=context.conversation_id,
    )
    assert result["decision"] == "DENY"
    assert result["executed"] is False


# ── Email: the same promise, kept the other way ───────────────────────────
#
# Nothing below is a journey. An email surface has no native control to press
# and no second message to press it in: the question travels inside the single
# composed reply, and the person answers by replying. Kept as written.


async def test_request_approval_on_resend_completes_in_the_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer request_approval (agent has no
    USER_INTERACTION toolset) — the agent must complete via its reply tool."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-approval-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[script_text("Here is my answer.")],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "Here is my answer." in json.dumps(resend_messages[-1])


async def test_an_emailed_approve_resolves_the_approval_despite_the_quoted_thread(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_resend,
    message_store,
    monkeypatch,
):
    """The round trip that went wrong in real use, with a real reply body.

    Email can be asked for approval now, and the answer comes back as an
    ordinary reply — which carries the quoted thread under it. Gmail soft-wraps
    a long attribution mid-address, so the reply arrived as

        approve\\n\\nOn Wed, Aug 26, 2026 at 12:26 AM butler via Lemma <
        butler.lemma2@ops.lemma.work> wrote:

    "approve" was no longer the message, so it stopped being a decision, fell
    through to the ordinary message path, and superseded the approval it was
    answering. Every fixture wrote that attribution on one line, which is why
    nothing caught it.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-approval-quoted-1",
                text="Please show me the widget.",
            ),
            headers={},
        ),
        script=approval_script("Done — approved and shown."),
    )
    conversation_id = str(context.conversation_id)
    await wait_for_messages(message_store, "RESEND", min_count=1)

    # The reply as a mail client actually sends it: the answer on top, then the
    # attribution wrapped mid-address, then the quoted body.
    quoted_reply = (
        "approve\n\n"
        f"On Wed, Aug 26, 2026 at 12:26 AM assistant via Lemma <\n"
        f"{assistant_address}> wrote:\n"
        "> Approval needed: Show a widget\n"
        '> Reply "approve" to run it, or "deny" to cancel.\n'
    )
    # Deliberately not `process_ingress_and_run_scripted`: resolving an approval
    # defers reconciliation to a worker, so there is no RUNNING run for the
    # helper to drive. The decision itself is recorded synchronously, and the
    # decision is what this test is about.
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_ingress(uow)
    reply_context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-approval-quoted-2",
                text=quoted_reply,
                subject="Re: Surface Resend E2E",
                in_reply_to="<resend-approval-quoted-1@resend-e2e.test>",
                references=["<resend-approval-quoted-1@resend-e2e.test>"],
            ),
            headers={},
        )
    )
    assert reply_context is not None
    await uow.commit()
    assert str(reply_context.conversation_id) == conversation_id, (
        "the reply must land in the conversation it answers, not a new one"
    )
    with suppress_agent_run_enqueue():
        await handler.execute_chat(reply_context)
    await db_session.commit()

    decision = (
        await db_session.execute(
            text(
                "SELECT decision FROM agent_approval_decisions "
                "WHERE conversation_id = :cid ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": conversation_id},
        )
    ).scalar_one_or_none()
    assert decision is not None, (
        "the emailed 'approve' recorded no decision at all -- it was read as an "
        "ordinary message and superseded the approval it was answering"
    )
    assert "APPROVE" in str(decision), f"read as {decision!r}, not an approval"
