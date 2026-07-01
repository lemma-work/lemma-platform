"""Unit tests for conversation workspace/pod cwd resolution."""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.services.workspace_location import (
    generate_cwd_slug,
    pod_cwd_from_workspace_cwd,
    resolve_pod_cwd,
    resolve_workspace_location,
)


def test_defaults_to_pretty_conversation_scoped_cwd_and_single_workspace():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    location = resolve_workspace_location(conversation)

    date = conversation.created_at.date().isoformat()
    assert location.cwd.startswith(f"/workspace/c/{date}/")
    # /workspace/c/{date}/{slug}
    assert location.cwd.count("/") == 4
    assert location.workspace_id == "default"


def test_conversation_metadata_overrides_cwd_and_workspace():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"cwd": "/workspace/project", "workspace_name": "research"},
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == "/workspace/project"
    assert location.workspace_id == "research"


def test_nested_workspace_block_takes_precedence():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"workspace": {"id": "ws-7", "cwd": "/workspace/ws7"}, "cwd": "/ignored"},
    )

    location = resolve_workspace_location(conversation)

    assert location.workspace_id == "ws-7"
    assert location.cwd == "/workspace/ws7"


def test_pod_cwd_mirrors_persisted_workspace_cwd_under_me():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"cwd": "/workspace/c/2026-07-02/ab3f2k7q"},
    )

    assert resolve_pod_cwd(conversation) == "/me/c/2026-07-02/ab3f2k7q"


def test_pod_cwd_mirrors_overridden_workspace_cwd():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"cwd": "/workspace/project"},
    )

    assert resolve_pod_cwd(conversation) == "/me/project"


def test_pod_cwd_from_workspace_cwd_edge_cases():
    assert pod_cwd_from_workspace_cwd("/workspace") == "/me"
    assert pod_cwd_from_workspace_cwd("/workspace/a/b") == "/me/a/b"
    # A cwd not under /workspace is placed under /me as-is (defensive).
    assert pod_cwd_from_workspace_cwd("/other/x") == "/me/other/x"


def test_generate_cwd_slug_is_short_and_alphanumeric():
    slug = generate_cwd_slug()

    assert len(slug) == 8
    assert slug.isalnum()
    assert slug == slug.lower()
