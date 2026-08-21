"""Outbound email attachment resolution: size gating that prevents a hard send
failure (D2/D3) and the Composio URL resolver (D1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.platforms.email_attachments import (
    resolve_outbound_email_attachment_urls,
    resolve_outbound_email_attachments,
)

pytestmark = pytest.mark.asyncio


async def test_oversize_workspace_file_is_skipped_not_inlined():
    """An oversize workspace file (can't be signed into a link) is skipped rather
    than inlined at full size — otherwise it would blow the provider limit and
    fail the whole email."""
    big = b"x" * 20
    deps = SimpleNamespace(
        pod_id=uuid4(),
        file_manager=SimpleNamespace(read_file=AsyncMock(return_value=big)),
    )
    inline, links = await resolve_outbound_email_attachments(
        deps, ["work.bin"], inline_cap_bytes=5
    )
    assert inline == []
    assert links == []


async def test_small_workspace_file_is_inlined():
    small = b"hello"
    deps = SimpleNamespace(
        pod_id=uuid4(),
        file_manager=SimpleNamespace(read_file=AsyncMock(return_value=small)),
    )
    inline, links = await resolve_outbound_email_attachments(
        deps, ["note.txt"], inline_cap_bytes=1024
    )
    assert len(inline) == 1
    assert inline[0][0] == "note.txt"
    assert inline[0][1] == small


async def test_workspace_file_is_unresolved_for_composio_urls():
    """Composio URL resolution can only sign datastore files; workspace files
    come back as unresolved names (the caller notes them in the body)."""
    deps = SimpleNamespace(pod_id=uuid4())
    resolved, unresolved = await resolve_outbound_email_attachment_urls(
        deps, ["work.bin"]
    )
    assert resolved == []
    assert unresolved == ["work.bin"]
