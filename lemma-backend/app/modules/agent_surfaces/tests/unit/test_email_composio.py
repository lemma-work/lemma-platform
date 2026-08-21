"""Composio-backed email surface I/O.

Email surfaces connected through Composio cannot call the Microsoft Graph /
Gmail REST APIs directly (Composio never exposes the provider OAuth token), so
the platform services dispatch reply / fetch / attachment-download through
Composio operations instead. These tests pin that dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.modules.agent_surfaces.platforms.gmail.service as gmail_service
import app.modules.agent_surfaces.platforms.outlook.service as outlook_service
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    GmailSurfaceEventMetadata,
    OutlookSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.gmail.tools import build_gmail_surface_toolset
from app.modules.agent_surfaces.platforms.outlook.tools import (
    build_outlook_surface_toolset,
)
from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager

_COMPOSIO_CREDS = {"provider": "COMPOSIO", "connection_id": "ca_test"}

pytestmark = pytest.mark.asyncio


def _capture_executor(calls: list[dict], result):
    async def _exec(*, connector_id, operation_name, payload, credentials):
        calls.append(
            {
                "connector_id": connector_id,
                "operation_name": operation_name,
                "payload": payload,
                "credentials": credentials,
            }
        )
        return result

    return _exec


async def test_outlook_reply_email_uses_composio_operation(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    tool = build_outlook_surface_toolset(credentials=dict(_COMPOSIO_CREDS)).tools[
        "outlook_reply_email"
    ]
    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="OUTLOOK",
            external_channel_id="assistant@outlook.test",
            external_thread_id="thread-1",
            surface_metadata=OutlookSurfaceEventMetadata(
                mailbox_email="assistant@outlook.test",
                subject="Need review",
                thread_id="thread-1",
                message_id="graph-msg-1",
                internet_message_id="<m1@example.com>",
                reply_to_email="rahul@example.com",
            ),
        )
    )
    request = SimpleNamespace(
        content="## Done\nAll set.",
        content_type="markdown",
        attachment_paths=[],
        subject=None,
    )

    response = await tool.function(ctx, request)

    assert response.success is True
    assert len(calls) == 1
    assert calls[0]["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert calls[0]["payload"]["message_id"] == "graph-msg-1"
    assert calls[0]["payload"]["is_html"] is True
    assert "Done" in calls[0]["payload"]["comment"]


def _outlook_ctx():
    return SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="OUTLOOK",
            external_channel_id="assistant@outlook.test",
            external_thread_id="thread-1",
            surface_metadata=OutlookSurfaceEventMetadata(
                mailbox_email="assistant@outlook.test",
                thread_id="thread-1",
                message_id="graph-msg-1",
                internet_message_id="<m1@example.com>",
                reply_to_email="rahul@example.com",
            ),
        )
    )


async def test_outlook_reply_email_composio_attaches_datastore_url(monkeypatch):
    """A datastore attachment is delivered to Composio as a signed URL in the
    `attachment` field (the SDK downloads + attaches it)."""
    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    async def fake_urls(deps, paths):
        return [("report.pdf", "https://signed.example/report.pdf")], []

    monkeypatch.setattr(
        outlook_service, "resolve_outbound_email_attachment_urls", fake_urls
    )

    tool = build_outlook_surface_toolset(credentials=dict(_COMPOSIO_CREDS)).tools[
        "outlook_reply_email"
    ]
    request = SimpleNamespace(
        content="hi",
        content_type="markdown",
        attachment_paths=["/me/report.pdf"],
        subject=None,
    )

    response = await tool.function(_outlook_ctx(), request)

    assert response.success is True
    assert response.attachment_count == 1
    assert calls[0]["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert calls[0]["payload"]["attachment"] == "https://signed.example/report.pdf"


async def test_outlook_reply_email_composio_notes_unattachable_workspace_file(
    monkeypatch,
):
    """A workspace file can't be signed into a URL, so it's noted in the body and
    the reply still sends (no silent drop, no hard failure)."""
    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    async def fake_read_file(self, path: str):
        return "body"

    monkeypatch.setattr(WorkspaceFileManager, "read_file", fake_read_file)

    tool = build_outlook_surface_toolset(credentials=dict(_COMPOSIO_CREDS)).tools[
        "outlook_reply_email"
    ]
    request = SimpleNamespace(
        content="hi",
        content_type="markdown",
        attachment_paths=["a.txt"],
        subject=None,
    )

    response = await tool.function(_outlook_ctx(), request)

    assert response.success is True
    assert response.attachment_count == 0
    assert "attachment" not in calls[0]["payload"]
    assert "Could not attach: a.txt" in calls[0]["payload"]["comment"]


async def test_gmail_reply_email_composio_attaches_datastore_url(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        gmail_service,
        "execute_composio_operation",
        _capture_executor(calls, {"id": "gmail-sent-1"}),
    )

    async def fake_urls(deps, paths):
        return [("report.pdf", "https://signed.example/report.pdf")], []

    monkeypatch.setattr(
        gmail_service, "resolve_outbound_email_attachment_urls", fake_urls
    )

    tool = build_gmail_surface_toolset(credentials=dict(_COMPOSIO_CREDS)).tools[
        "gmail_reply_email"
    ]
    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="GMAIL",
            external_channel_id="assistant@gmail.test",
            external_thread_id="gmail-thread-1",
            surface_metadata=GmailSurfaceEventMetadata(
                mailbox_email="assistant@gmail.test",
                subject="Need review",
                thread_id="gmail-thread-1",
                message_id="gmail-message-1",
                reply_to_email="rahul@example.com",
            ),
        )
    )
    request = SimpleNamespace(
        content="## Done",
        content_type="markdown",
        attachment_paths=["/me/report.pdf"],
        subject=None,
    )

    response = await tool.function(ctx, request)

    assert response.success is True
    assert response.attachment_count == 1
    assert calls[0]["operation_name"] == "GMAIL_REPLY_TO_THREAD"
    assert calls[0]["payload"]["attachment"] == "https://signed.example/report.pdf"


async def test_gmail_reply_email_uses_composio_operation(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        gmail_service,
        "execute_composio_operation",
        _capture_executor(calls, {"id": "gmail-sent-1"}),
    )

    tool = build_gmail_surface_toolset(credentials=dict(_COMPOSIO_CREDS)).tools[
        "gmail_reply_email"
    ]
    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="GMAIL",
            external_channel_id="assistant@gmail.test",
            external_thread_id="gmail-thread-1",
            surface_metadata=GmailSurfaceEventMetadata(
                mailbox_email="assistant@gmail.test",
                subject="Need review",
                thread_id="gmail-thread-1",
                message_id="gmail-message-1",
                reply_to_email="rahul@example.com",
            ),
        )
    )
    request = SimpleNamespace(
        content="## Done",
        content_type="markdown",
        attachment_paths=[],
        subject=None,
    )

    response = await tool.function(ctx, request)

    assert response.success is True
    assert len(calls) == 1
    assert calls[0]["operation_name"] == "GMAIL_REPLY_TO_THREAD"
    assert calls[0]["payload"]["thread_id"] == "gmail-thread-1"
    assert calls[0]["payload"]["recipient_email"] == "rahul@example.com"
    assert calls[0]["payload"]["is_html"] is True
