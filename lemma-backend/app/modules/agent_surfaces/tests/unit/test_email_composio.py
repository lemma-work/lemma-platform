"""Composio-backed email surface I/O.

Email surfaces connected through Composio cannot call the Microsoft Graph /
Gmail REST APIs directly (Composio never exposes the provider OAuth token), so
the platform services dispatch reply / fetch / attachment-download through
Composio operations instead. These tests pin that dispatch.
"""

from __future__ import annotations


import pytest

import app.modules.agent_surfaces.platforms.outlook.service as outlook_service

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


def _email_event(platform: str, **reply_target):
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )

    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        message_text="Need review",
        reply_target=dict(reply_target),
    )


def _envelope(text: str, *files):
    from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope

    return SurfaceEnvelope(text=text, files=list(files))


def _file(name: str, *, signed_url: str | None = None):
    from app.modules.agent_surfaces.domain.envelope import EnvelopeFile

    return EnvelopeFile(
        file_name=name,
        content=b"bytes",
        mime_type="text/plain",
        source_path=f"/me/{name}",
        signed_url=signed_url,
    )


async def test_a_composio_outlook_envelope_goes_through_the_composio_operation(
    monkeypatch,
):
    """Same transport as before; the run observer drives it, not a reply tool."""
    from app.modules.agent_surfaces.platforms.outlook.adapter import (
        ComposioOutlookSurfaceAdapter,
    )

    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    await ComposioOutlookSurfaceAdapter().deliver(
        credentials=dict(_COMPOSIO_CREDS),
        event=_email_event(
            "OUTLOOK", recipient_email="rahul@example.com", message_id="graph-msg-1"
        ),
        envelope=_envelope("## Done\nAll set."),
    )

    assert len(calls) == 1
    assert calls[0]["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert calls[0]["payload"]["message_id"] == "graph-msg-1"
    assert calls[0]["payload"]["is_html"] is True
    assert "Done" in calls[0]["payload"]["comment"]


async def test_a_composio_account_attaches_the_signed_url_not_the_bytes(monkeypatch):
    """Composio downloads a link server-side; it cannot take content at all.

    Which is why EnvelopeFile carries a signed_url alongside its bytes, resolved
    by the caller that had the pod.
    """
    from app.modules.agent_surfaces.platforms.outlook.adapter import (
        ComposioOutlookSurfaceAdapter,
    )

    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    await ComposioOutlookSurfaceAdapter().deliver(
        credentials=dict(_COMPOSIO_CREDS),
        event=_email_event(
            "OUTLOOK", recipient_email="rahul@example.com", message_id="graph-msg-1"
        ),
        envelope=_envelope(
            "See attached.", _file("q3.pdf", signed_url="https://signed.test/q3")
        ),
    )

    assert calls[0]["payload"]["attachment"] == "https://signed.test/q3"


async def test_a_second_file_becomes_a_link_because_composio_takes_one(monkeypatch):
    from app.modules.agent_surfaces.platforms.outlook.adapter import (
        ComposioOutlookSurfaceAdapter,
    )

    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    await ComposioOutlookSurfaceAdapter().deliver(
        credentials=dict(_COMPOSIO_CREDS),
        event=_email_event(
            "OUTLOOK", recipient_email="rahul@example.com", message_id="graph-msg-1"
        ),
        envelope=_envelope(
            "See attached.",
            _file("q3.pdf", signed_url="https://signed.test/q3"),
            _file("q4.pdf", signed_url="https://signed.test/q4"),
        ),
    )

    body = calls[0]["payload"]["comment"]
    assert calls[0]["payload"]["attachment"] == "https://signed.test/q3"
    assert "https://signed.test/q4" in body


async def test_a_file_that_could_not_be_signed_is_named_rather_than_dropped(
    monkeypatch,
):
    """Silence would leave the recipient unaware a file was meant to be there."""
    from app.modules.agent_surfaces.platforms.outlook.adapter import (
        ComposioOutlookSurfaceAdapter,
    )

    calls: list[dict] = []
    monkeypatch.setattr(
        outlook_service, "execute_composio_operation", _capture_executor(calls, None)
    )

    await ComposioOutlookSurfaceAdapter().deliver(
        credentials=dict(_COMPOSIO_CREDS),
        event=_email_event(
            "OUTLOOK", recipient_email="rahul@example.com", message_id="graph-msg-1"
        ),
        envelope=_envelope("See attached.", _file("local-only.txt")),
    )

    assert "Could not attach: local-only.txt" in calls[0]["payload"]["comment"]
