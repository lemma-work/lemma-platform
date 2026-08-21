"""Unit tests for conversation workspace/pod cwd resolution."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.workspace_location import (
    ProjectRepo,
    generate_cwd_slug,
    parse_project_repo,
    pod_cwd_from_workspace_cwd,
    resolve_pod_cwd,
    resolve_workspace_location,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    workspace_runtime_context,
)


def test_defaults_to_pretty_conversation_scoped_cwd_and_single_workspace():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    location = resolve_workspace_location(conversation)

    date = conversation.created_at.date().isoformat()
    assert location.cwd.startswith(f"/workspace/c/{date}/")
    # /workspace/c/{date}/{slug}
    assert location.cwd.count("/") == 4
    assert location.workspace_id == "default"


def test_legacy_conversation_without_cwd_resolves_stably():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    first = resolve_workspace_location(conversation)
    second = resolve_workspace_location(conversation)

    assert first.cwd == second.cwd
    assert first.cwd.endswith(conversation.id.hex[:8])


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
        metadata={
            "workspace": {"id": "ws-7", "cwd": "/workspace/ws7"},
            "cwd": "/ignored",
        },
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


def test_python_runtime_identity_tracks_conversation_working_directory():
    conversation_id = uuid4()
    common = {
        "user_id": uuid4(),
        "org_id": uuid4(),
        "pod_id": uuid4(),
        "conversation_id": conversation_id,
        "agent_name": "builder",
    }
    first = workspace_runtime_context(
        BaseAgentContext(
            **common,
            workspace_cwd="/workspace/conversations/first",
        )
    )
    second = workspace_runtime_context(
        BaseAgentContext(
            **common,
            workspace_cwd="/workspace/conversations/second",
        )
    )

    assert first.initial_cwd == "/workspace/conversations/first"
    assert second.initial_cwd == "/workspace/conversations/second"
    assert first.default_python_session_id != second.default_python_session_id


class _StubConversationRepository:
    def __init__(self, conversations: dict) -> None:
        self._conversations = conversations

    async def get_conversation(self, conversation_id, **_kwargs):
        return self._conversations.get(conversation_id)


def _service(conversations: dict):
    return ConversationService(
        uow=None,  # type: ignore[arg-type]
        conversation_repository=_StubConversationRepository(  # type: ignore[arg-type]
            conversations
        ),
        agent_repository=None,  # type: ignore[arg-type]
        authorization_service=None,
    )


async def test_subagent_and_grandchild_share_the_parent_working_directory():
    """A sub-agent must land in the directory its parent is already working in.

    Sub-agents share the user's single sandbox, so running one in its own
    fresh directory would strand it away from the files the parent asked it to
    work on. Nothing else guards this, and the cwd is stamped once at creation
    and read back forever after.
    """

    parent = Conversation(pod_id=uuid4(), user_id=uuid4())
    service = _service({})
    await service._apply_inherited_cwd(parent, parent_id=None)

    child = Conversation(pod_id=parent.pod_id, user_id=parent.user_id)
    service = _service({parent.id: parent})
    await service._apply_inherited_cwd(child, parent_id=parent.id)

    grandchild = Conversation(pod_id=parent.pod_id, user_id=parent.user_id)
    service = _service({parent.id: parent, child.id: child})
    await service._apply_inherited_cwd(grandchild, parent_id=child.id)

    parent_cwd = resolve_workspace_location(parent).cwd
    assert resolve_workspace_location(child).cwd == parent_cwd
    assert resolve_workspace_location(grandchild).cwd == parent_cwd
    # The pod filesystem is derived from the same value, so both filesystems
    # stay aligned for the whole family.
    assert resolve_pod_cwd(grandchild) == resolve_pod_cwd(parent)


async def test_subagent_shell_and_python_runtimes_use_the_inherited_directory():
    """The inherited cwd must actually reach the workspace tool runtime."""

    parent = Conversation(pod_id=uuid4(), user_id=uuid4())
    service = _service({})
    await service._apply_inherited_cwd(parent, parent_id=None)
    child = Conversation(pod_id=parent.pod_id, user_id=parent.user_id)
    service = _service({parent.id: parent})
    await service._apply_inherited_cwd(child, parent_id=parent.id)

    contexts = [
        workspace_runtime_context(
            BaseAgentContext(
                user_id=conversation.user_id,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                workspace_cwd=resolve_workspace_location(conversation).cwd,
            )
        )
        for conversation in (parent, child)
    ]

    assert contexts[0].initial_cwd == contexts[1].initial_cwd
    # Separate conversations still get separate interpreters and shells; only
    # the directory is shared.
    assert (
        contexts[0].default_python_session_id != contexts[1].default_python_session_id
    )
    assert contexts[0].default_shell_session_id != contexts[1].default_shell_session_id


def test_generate_cwd_slug_is_short_and_alphanumeric():
    slug = generate_cwd_slug()

    assert len(slug) == 8
    assert slug.isalnum()
    assert slug == slug.lower()


# --- project repos -----------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        # An owner/repo pair becomes a filesystem path, so anything that could
        # climb out of /workspace/repos must not survive parsing.
        {"owner": "../etc", "repo": "passwd"},
        {"owner": "acme", "repo": ".."},
        {"owner": "acme", "repo": "."},
        {"owner": "acme", "repo": "nested/path"},
        {"owner": "acme/other", "repo": "web"},
        {"owner": "", "repo": "web"},
        {"owner": "acme", "repo": ""},
        # A leading dash would be read by git as an option, not a name.
        {"owner": "-acme", "repo": "web"},
        {"repo": "web"},
        "acme/web",
        None,
    ],
)
def test_parse_project_repo_rejects_anything_unsafe(value):
    assert parse_project_repo(value) is None


def test_parse_project_repo_normalizes_a_pasted_repo_name():
    repo = parse_project_repo({"owner": "acme", "repo": "web.git", "ref": "main"})

    assert repo == ProjectRepo(owner="acme", repo="web", ref="main")
    assert repo.full_name == "acme/web"
    assert repo.cwd == "/workspace/repos/acme/web"


def test_parse_project_repo_drops_a_ref_git_would_read_as_a_flag():
    """A ref reaches `git clone --branch`, so `--upload-pack=...` is an exploit."""

    repo = parse_project_repo(
        {"owner": "acme", "repo": "web", "ref": "--upload-pack=touch /tmp/pwned"}
    )

    assert repo is not None
    assert repo.ref is None


def test_parse_project_repo_ignores_an_unparseable_account():
    repo = parse_project_repo({"owner": "acme", "repo": "web", "account_id": "nope"})

    assert repo is not None
    assert repo.account_id is None


def test_repo_metadata_derives_the_working_directory():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"repo": {"owner": "acme", "repo": "web", "ref": "main"}},
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == "/workspace/repos/acme/web"
    assert location.repo is not None
    assert location.repo.ref == "main"
    # Both filesystems still line up, exactly as they do for a scratchpad.
    assert resolve_pod_cwd(conversation) == "/me/repos/acme/web"


def test_an_explicit_cwd_still_wins_over_the_repo_directory():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={
            "repo": {"owner": "acme", "repo": "web"},
            "cwd": "/workspace/somewhere-else",
        },
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == "/workspace/somewhere-else"
    # The repo is still in effect — the checkout just lands where it was asked to.
    assert location.repo is not None


async def test_creating_against_a_repo_stamps_the_directory_and_a_filterable_name():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"repo": {"owner": "acme", "repo": "web.git"}},
    )
    service = _service({})

    await service._apply_inherited_cwd(conversation, parent_id=None)

    assert conversation.metadata["cwd"] == "/workspace/repos/acme/web"
    # Rewritten from the parsed form, not stored as the client sent it.
    assert conversation.metadata["repo"] == {"owner": "acme", "repo": "web"}
    # Flat, because conversation listing filters metadata by JSONB containment
    # over string values only — a nested key cannot be queried.
    assert conversation.metadata["repo_full_name"] == "acme/web"


async def test_an_unusable_repo_leaves_the_conversation_on_a_scratchpad():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"repo": {"owner": "../etc", "repo": "passwd"}},
    )
    service = _service({})

    await service._apply_inherited_cwd(conversation, parent_id=None)

    assert "repo" not in conversation.metadata
    assert "repo_full_name" not in conversation.metadata
    assert conversation.metadata["cwd"].startswith("/workspace/c/")


async def test_a_subagent_inherits_the_parent_project():
    parent = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"repo": {"owner": "acme", "repo": "web", "ref": "main"}},
    )
    service = _service({})
    await service._apply_inherited_cwd(parent, parent_id=None)

    child = Conversation(pod_id=parent.pod_id, user_id=parent.user_id)
    service = _service({parent.id: parent})
    await service._apply_inherited_cwd(child, parent_id=parent.id)

    child_location = resolve_workspace_location(child)
    assert child_location.cwd == "/workspace/repos/acme/web"
    assert child_location.repo == resolve_workspace_location(parent).repo
    assert child.metadata["repo_full_name"] == "acme/web"
