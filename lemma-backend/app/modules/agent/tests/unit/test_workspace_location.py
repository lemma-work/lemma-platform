"""Unit tests for conversation workspace/pod cwd resolution."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tests.unit.repository_fakes import wire_repositories
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.application.conversations.start import StartConversation
from app.modules.agent.domain.workspace_location import (
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
from sandbox_runtime.paths import WORKSPACE_ROOT


def test_defaults_to_pretty_conversation_scoped_cwd_and_single_workspace():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    location = resolve_workspace_location(conversation)

    date = conversation.created_at.date().isoformat()
    assert location.cwd.startswith(f"{WORKSPACE_ROOT}/c/{date}/")
    # <root>/c/{date}/{slug}
    # <root>/c/{date}/{slug}: one segment past the root, whatever the root is.
    assert location.cwd.count("/") == WORKSPACE_ROOT.count("/") + 3
    assert location.workspace_id == "default"


def test_legacy_conversation_without_cwd_resolves_stably():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    first = resolve_workspace_location(conversation)
    second = resolve_workspace_location(conversation)

    assert first.cwd == second.cwd
    assert first.cwd.endswith(conversation.id.hex[:8])


def test_the_recorded_cwd_and_workspace_override_the_defaults():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        cwd=f"{WORKSPACE_ROOT}/project",
        workspace_name="research",
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == f"{WORKSPACE_ROOT}/project"
    assert location.workspace_id == "research"


def test_a_workspace_id_wins_over_its_name():
    conversation = Conversation(
        pod_id=uuid4(), user_id=uuid4(), workspace_id="ws-7", workspace_name="x"
    )

    assert resolve_workspace_location(conversation).workspace_id == "ws-7"


def test_a_cwd_written_into_metadata_moves_nothing():
    """Where a conversation works is its own column; a client's tag is not it."""
    conversation = Conversation(
        pod_id=uuid4(), user_id=uuid4(), metadata={"cwd": "/etc", "workspace_id": "x"}
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd.startswith(f"{WORKSPACE_ROOT}/c/")
    assert location.workspace_id == "default"


def test_pod_cwd_mirrors_persisted_workspace_cwd_under_me():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        cwd=f"{WORKSPACE_ROOT}/c/2026-07-02/ab3f2k7q",
    )

    assert resolve_pod_cwd(conversation) == "/me/c/2026-07-02/ab3f2k7q"


def test_pod_cwd_mirrors_overridden_workspace_cwd():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        cwd=f"{WORKSPACE_ROOT}/project",
    )

    assert resolve_pod_cwd(conversation) == "/me/project"


def test_a_context_without_a_conversation_falls_back_to_the_project_root():
    """Not to a directory named after the conversation id.

    `resolve_workspace_location` puts conversations at `c/{date}/{slug}`, so a
    fallback of `<root>/conversations/<uuid>` named a directory nothing else in
    the system produces -- and the one caller that reaches it, the pod MCP
    bridge, carries a *nil* conversation id, so the directory it named was all
    zeroes. The root is the honest answer to "no conversation".
    """
    from app.modules.agent.tools.context import BaseAgentContext

    ctx = BaseAgentContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())

    assert ctx.get_workspace_cwd() == WORKSPACE_ROOT
    assert "/conversations/" not in ctx.get_workspace_cwd()
    assert str(ctx.conversation_id) not in ctx.get_workspace_cwd()


def test_pod_cwd_from_workspace_cwd_edge_cases():
    assert pod_cwd_from_workspace_cwd(WORKSPACE_ROOT) == "/me"
    assert pod_cwd_from_workspace_cwd(f"{WORKSPACE_ROOT}/a/b") == "/me/a/b"
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
            workspace_cwd=f"{WORKSPACE_ROOT}/conversations/first",
        )
    )
    second = workspace_runtime_context(
        BaseAgentContext(
            **common,
            workspace_cwd=f"{WORKSPACE_ROOT}/conversations/second",
        )
    )

    assert first.initial_cwd == f"{WORKSPACE_ROOT}/conversations/first"
    assert second.initial_cwd == f"{WORKSPACE_ROOT}/conversations/second"
    assert first.default_python_session_id != second.default_python_session_id


class _StubConversations:
    def __init__(self, conversations: dict) -> None:
        self._conversations = conversations

    async def get_conversation(self, conversation_id, **_kwargs):
        return self._conversations.get(conversation_id)


def _service(conversations: dict):
    return wire_repositories(
        StartConversation(
            None,  # type: ignore[arg-type]
            agent_repository=None,  # type: ignore[arg-type]
        ),
        _StubConversations(conversations),
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
    assert repo.cwd == f"{WORKSPACE_ROOT}/repos/acme/web"


def test_parse_project_repo_drops_a_ref_git_would_read_as_a_flag():
    """A ref reaches `git clone --branch`, so `--upload-pack=...` is an exploit."""

    repo = parse_project_repo(
        {"owner": "acme", "repo": "web", "ref": "--upload-pack=touch /tmp/pwned"}
    )

    assert repo is not None
    assert repo.ref is None


def test_a_project_never_names_the_account_it_clones_as():
    """Which account clones is the pod's `github` connector's decision, so an
    account a client writes into the metadata is not carried anywhere."""
    repo = parse_project_repo(
        {"owner": "acme", "repo": "web", "account_id": str(uuid4())}
    )

    assert repo == ProjectRepo(owner="acme", repo="web")
    assert repo.to_json() == {"owner": "acme", "repo": "web"}


def test_repo_metadata_derives_the_working_directory():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        repo=ProjectRepo(owner="acme", repo="web", ref="main"),
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == f"{WORKSPACE_ROOT}/repos/acme/web"
    assert location.repo is not None
    assert location.repo.ref == "main"
    # Both filesystems still line up, exactly as they do for a scratchpad.
    assert resolve_pod_cwd(conversation) == "/me/repos/acme/web"


def test_an_explicit_cwd_still_wins_over_the_repo_directory():
    conversation = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        repo=ProjectRepo(owner="acme", repo="web"),
        cwd=f"{WORKSPACE_ROOT}/somewhere-else",
    )

    location = resolve_workspace_location(conversation)

    assert location.cwd == f"{WORKSPACE_ROOT}/somewhere-else"
    # The repo is still in effect — the checkout just lands where it was asked to.
    assert location.repo is not None


async def test_creating_against_a_repo_stamps_its_directory():
    conversation = Conversation(
        pod_id=uuid4(), user_id=uuid4(), repo=ProjectRepo(owner="acme", repo="web")
    )
    service = _service({})

    await service._apply_inherited_cwd(conversation, parent_id=None)

    assert conversation.cwd == f"{WORKSPACE_ROOT}/repos/acme/web"


async def test_a_root_conversation_gets_a_scratchpad_of_its_own():
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())
    service = _service({})

    await service._apply_inherited_cwd(conversation, parent_id=None)

    assert conversation.repo is None
    assert conversation.cwd is not None
    assert conversation.cwd.startswith(f"{WORKSPACE_ROOT}/c/")


async def test_a_subagent_inherits_the_parent_project():
    parent = Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        repo=ProjectRepo(owner="acme", repo="web", ref="main"),
    )
    service = _service({})
    await service._apply_inherited_cwd(parent, parent_id=None)

    child = Conversation(pod_id=parent.pod_id, user_id=parent.user_id)
    service = _service({parent.id: parent})
    await service._apply_inherited_cwd(child, parent_id=parent.id)

    child_location = resolve_workspace_location(child)
    assert child_location.cwd == f"{WORKSPACE_ROOT}/repos/acme/web"
    assert child_location.repo == resolve_workspace_location(parent).repo
    assert child.repo == parent.repo
