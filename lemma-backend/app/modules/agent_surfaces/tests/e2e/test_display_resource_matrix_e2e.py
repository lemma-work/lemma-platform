"""``display_resource(type=FILE)``: attached natively, or degraded to a link.

The tool checks the size and the surface decides whether it can send the bytes
at all. Those two together are the matrix, and they read as a table: Slack and
Telegram attach a small file and fall back to a link card for a large one;
WhatsApp attaches; Teams has no native file send whatever the size, so every
Teams file is a link.

This supersedes ``test_surface_file_egress_e2e.py``, which called the surface
handler's delivery method directly and so exercised neither the tool's size
check nor the native-vs-link decision. Here `display_resource` is a genuine
scripted LLM tool call and both run for real.

The oversize cases lower ``SURFACE_INLINE_SOFT_BYTE_CAP`` rather than seeding a
file past the real 20 MB cap: twenty megabytes through the datastore to prove a
branch that reads one integer is memory and wall clock for nothing.

Two cells are deliberately absent:

- **WhatsApp's per-media-kind thresholds** are unit-tested, not covered here.
  ``fits_inline`` is not uniform -- WhatsApp caps an image at 5 MB and a
  document at 100 MB -- so its threshold is the one that does *not* follow from
  the Slack and Telegram cases. That branch is proven in
  ``tests/unit/test_attachment_limits.py``; an e2e case driving an oversize
  WhatsApp image through a real upload rejection is a genuine gap.
- **Composio-connected Gmail/Outlook attach exactly one file, by URL.**
  Composio's action takes a signed URL and fetches it server-side, so any file
  after the first is appended to the body as a link (see
  ``GmailPlatformService._resolve_reply_attachments``). Workspace paths cannot
  be signed and come back as an "unresolved" note instead -- also uncovered.

The pod-resource catalog and the email attachment keep their own tests below:
neither is about size.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from httpx import AsyncClient
from sandbox_runtime.protocol import (
    PortAccessGrant,
    PortProtocol,
    SandboxKey,
    WorkloadKind,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.tools.user_interaction import (
    pydantic_adapter as user_interaction_adapter,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.platforms import attachment_limits
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _messages_for_conversation,
    _resend_payload,
    _seed_pod_file,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_display_resource,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface
from app.modules.connectors.domain.connector import AuthProvider

pytestmark = pytest.mark.e2e

_TOOL_CALL_ID = "tool-display-1"

#: Threshold the oversize cases are driven at. `inline_cap` reads the module
#: global per call, which is what makes lowering it work.
_TINY_INLINE_CAP_BYTES = 2048

#: Where a link card points. Any file the surface cannot send natively becomes
#: one of these.
LINK_HOST = "app.example.test"


#: platform -> (bucket a native attachment lands in, bucket a reply lands in).
#: Teams has no native file send at all -- `TeamsSurfaceAdapter` does not
#: override `_render_file`, so the base stub always returns False -- which is
#: why its native bucket is None and it has no size-threshold case to prove.
DELIVERY = {
    SurfacePlatform.SLACK: ("SLACK_FILE_UPLOAD_URL", "SLACK"),
    SurfacePlatform.TELEGRAM: ("TELEGRAM_FILE", "TELEGRAM"),
    SurfacePlatform.WHATSAPP: ("WHATSAPP_MEDIA_UPLOAD", "WHATSAPP"),
    SurfacePlatform.TEAMS: (None, "TEAMS"),
}

#: The cases, as (platform, oversize). Teams appears once: there is no
#: threshold behaviour to prove when the answer is always a link.
SIZE_CASES = [
    (SurfacePlatform.SLACK, False),
    (SurfacePlatform.SLACK, True),
    (SurfacePlatform.TELEGRAM, False),
    (SurfacePlatform.TELEGRAM, True),
    (SurfacePlatform.WHATSAPP, False),
    (SurfacePlatform.TEAMS, True),
]


@pytest.mark.parametrize(
    ("platform", "oversize"),
    SIZE_CASES,
    ids=lambda value: (
        value.value
        if isinstance(value, SurfacePlatform)
        else ("oversize" if value else "small")
    ),
)
async def test_a_file_is_attached_natively_or_degrades_to_a_link(
    platform: SurfacePlatform,
    oversize: bool,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    native_bucket, reply_bucket = DELIVERY[platform]
    expect_native = native_bucket is not None and not oversize
    if oversize:
        monkeypatch.setattr(
            attachment_limits, "SURFACE_INLINE_SOFT_BYTE_CAP", _TINY_INLINE_CAP_BYTES
        )
    content = b"x" * (_TINY_INLINE_CAP_BYTES + 1024) if oversize else b"%PDF-1.4 tiny"
    name = "large.pdf" if oversize else "small.pdf"

    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=stage.pod_id,
        name=name,
        content=content,
    )

    await stage.say(
        "show the report",
        script=[
            script_display_resource(type="FILE", path=path, tool_call_id=_TOOL_CALL_ID),
            script_text("Here you go."),
        ],
    )

    if expect_native:
        uploaded = await wait_for_messages(message_store, native_bucket, min_count=1)
        assert uploaded[-1]["filename"] == name
        # And no link: the bytes went, so nothing had to stand in for them.
        replies = message_store.get_all(reply_bucket)
        assert not any(name in (message.get("text") or "") for message in replies), (
            f"{platform.value}: attached the file and still sent a link for it"
        )
        return

    if native_bucket is not None:
        assert message_store.get_all(native_bucket) == [], (
            f"{platform.value}: attempted a native upload it should have skipped"
        )
    replies = await wait_for_messages(message_store, reply_bucket, min_count=1)
    # The URL rides in the card's button rather than the notification-fallback
    # text, so this looks at the whole message.
    assert LINK_HOST in json.dumps(replies, default=str), (
        f"{platform.value}: no link card stood in for the file: {replies}"
    )


# -- Not about size: a catalog of pod resources, and email's one reply -----


async def test_display_resource_slack_routes_pod_resource_catalog_to_deep_links(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A catalog-style request renders every non-widget pod resource shape.

    This starts at the Slack ingress boundary, runs the real agent harness and
    ``display_resource`` tool, persists every tool result, and finally observes
    the platform cards sent to Slack.  It protects the frontend deep-link
    contract for named resources, collection views, filtered tables, read-only
    queries, and file-viewer fallbacks in one realistic user turn.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    browser_access_calls: list[tuple[UUID, int]] = []

    class _FakeWorkspaceSandboxService:
        """Keep this surface-delivery journey hermetic at the sandbox boundary."""

        async def create_browser_access(
            self,
            user_id: UUID,
            *,
            ttl_seconds: int,
        ) -> PortAccessGrant:
            browser_access_calls.append((user_id, ttl_seconds))
            return PortAccessGrant(
                key=SandboxKey(
                    workload_kind=WorkloadKind.WORKSPACE, logical_id=user_id
                ),
                port=4848,
                protocol=PortProtocol.HTTP,
                url="https://sandbox.example.test/port-access/browser-token",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        user_interaction_adapter,
        "WorkspaceSandboxService",
        _FakeWorkspaceSandboxService,
    )
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-resource-catalog",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )

    long_query = "SELECT * FROM incidents WHERE summary ILIKE '%outage%' " + (
        "AND resolved_at IS NULL " * 12
    )
    invalid_calls = [
        script_display_resource(
            type="BROWSER", name="browser", tool_call_id="tool-invalid-browser"
        ),
        script_display_resource(
            type="AGENT", path="/me/not-an-agent", tool_call_id="tool-invalid-path"
        ),
        script_display_resource(
            type="AGENT",
            content="<div>not a widget</div>",
            tool_call_id="tool-invalid-content",
        ),
        script_display_resource(
            type="AGENT",
            loading_messages=["Loading"],
            tool_call_id="tool-invalid-loading",
        ),
        script_display_resource(
            type="FILE",
            path="/private/tmp/report.pdf",
            tool_call_id="tool-invalid-private-file",
        ),
        script_display_resource(type="WIDGET", tool_call_id="tool-invalid-widget"),
        script_display_resource(
            type="AGENT", query="SELECT 1", tool_call_id="tool-invalid-query"
        ),
        script_display_resource(
            type="TABLE",
            name="incidents",
            filters=[{"field": "status", "op": "eq", "value": "OPEN"}],
            query="SELECT 1",
            tool_call_id="tool-invalid-table-combination",
        ),
        script_display_resource(
            type="TABLE",
            filters=[{"field": "status", "op": "eq", "value": "OPEN"}],
            tool_call_id="tool-invalid-table-filter",
        ),
    ]
    resource_calls = [
        script_display_resource(type="BROWSER", tool_call_id="tool-browser"),
        script_display_resource(
            type="TABLE",
            name="incidents",
            filters=[{"field": "status", "op": "eq", "value": "OPEN"}],
            tool_call_id="tool-table-filtered",
        ),
        script_display_resource(
            type="TABLE",
            query=long_query,
            tool_call_id="tool-table-query",
        ),
        script_display_resource(
            type="AGENT", name="incident-triage", tool_call_id="tool-agent"
        ),
        # Lowercase deliberately exercises the public model's case-insensitive
        # enum coercion, as model providers do not always preserve enum casing.
        script_display_resource(type="agent", tool_call_id="tool-agents"),
        script_display_resource(
            type="FUNCTION", name="summarize-incident", tool_call_id="tool-function"
        ),
        script_display_resource(type="FUNCTION", tool_call_id="tool-functions"),
        script_display_resource(
            type="WORKFLOW", name="incident-response", tool_call_id="tool-workflow"
        ),
        script_display_resource(type="WORKFLOW", tool_call_id="tool-workflows"),
        script_display_resource(
            type="APP", name="incident-dashboard", tool_call_id="tool-app"
        ),
        script_display_resource(type="APP", tool_call_id="tool-apps"),
        script_display_resource(
            type="SCHEDULE", name="daily-triage", tool_call_id="tool-schedule"
        ),
        script_display_resource(type="SCHEDULE", tool_call_id="tool-schedules"),
        script_display_resource(type="FILE", tool_call_id="tool-files"),
        script_display_resource(
            type="FILE",
            path=r"/pod//reports\quarterly.pdf",
            tool_call_id="tool-missing-file",
        ),
    ]
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=slack_payloads.dm(
                text="Show me the incident resources and current report views",
                ts="1700002250.600600",
            ),
            headers={},
        ),
        script=[
            *invalid_calls,
            *resource_calls,
            script_text("The incident catalog is ready."),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    tool_returns = [
        message
        for message in messages
        if message.get("kind") == "TOOL_RETURN"
        and str(message.get("tool_call_id", "")).startswith("tool-")
    ]
    assert len(tool_returns) == len(invalid_calls) + len(resource_calls)
    invalid_returns = [
        message
        for message in tool_returns
        if str(message["tool_call_id"]).startswith("tool-invalid-")
    ]
    assert len(invalid_returns) == len(invalid_calls)
    assert all(
        message["tool_result"]["success"] is False for message in invalid_returns
    )
    successful_returns = [
        message for message in tool_returns if message not in invalid_returns
    ]
    unexpected_failures = [
        {
            "tool_call_id": message["tool_call_id"],
            "tool_result": message["tool_result"],
        }
        for message in successful_returns
        if not message["tool_result"]["success"]
    ]
    assert unexpected_failures == []
    assert browser_access_calls == [(UUID(fixed_test_user["id"]), 1800)]

    # `min_count=len(resource_calls)`, not `+ 1`: only the resource cards go
    # out via chat.postMessage (the "SLACK" bucket). The final text reply
    # streams instead (chat.startStream/appendStream/stopStream, a separate
    # "SLACK_STREAM_APPEND" bucket) and is verified below via
    # wait_for_slack_text, which checks both transports. A `+ 1` here is
    # unreachable and previously burned the full timeout on every run,
    # silently, because wait_for_messages used to soft-return its
    # under-count on timeout instead of failing.
    slack_messages = await wait_for_messages(
        message_store, "SLACK", min_count=len(resource_calls)
    )
    rendered = json.dumps(slack_messages)
    # Every Lemma-owned resource gets a frontend deep link. BROWSER is the one
    # exception: it intentionally opens the short-lived sandbox settings asserted
    # separately below.
    assert rendered.count("https://app.example.test/pod/") >= len(resource_calls) - 1
    assert "incidents" in rendered
    assert "/port-access/" in rendered
    assert "incident-triage" in rendered
    assert "summarize-incident" in rendered
    assert "incident-response" in rendered
    assert "incident-dashboard" in rendered
    assert "daily-triage" in rendered
    assert "%2Freports%2Fquarterly.pdf" in rendered
    delivered = await wait_for_slack_text(
        message_store, "The incident catalog is ready."
    )
    assert sum("The incident catalog is ready." in text for text in delivered) == 1, (
        f"the final answer must land exactly once, got {delivered}"
    )


async def test_a_file_shown_on_email_is_attached_to_the_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Resend is not Composio-gated — attachment_paths bytes genuinely reach
    the outbound email as a base64 attachment."""
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-file-1",
                text="Can you send me the report?",
            ),
            headers={},
        ),
        # No reply tool any more: the agent shows the file and writes its
        # answer, and the one reply carries both.
        script=[
            script_display_resource(type="FILE", path=path, tool_call_id=_TOOL_CALL_ID),
            script_text("Here is the report."),
        ],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    attachments = resend_messages[-1].get("attachments") or []
    assert attachments
    assert attachments[0]["filename"] == "small.pdf"
