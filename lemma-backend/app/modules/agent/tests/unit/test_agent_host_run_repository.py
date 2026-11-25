"""The repository a dispatched run tells the Agent Host to check out.

A conversation opened against a repository works in a worktree of it, which
the cloud sandbox makes before the agent's first command. A coding agent on the
person's own computer starts in the same directory under ``~/lemma``, and the
host can only make it a worktree if the run spec names the repository and the
worktree's slug. It is sent only when the directory really is that worktree, so
the host is never asked to put a checkout somewhere the conversation did not
already work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid7

from app.modules.agent.agent_host.domain.host import (
    AgentHostRunRepository,
    AgentHostRunSpec,
)
from app.modules.agent.agent_host.harness.dispatch import run_spec_from
from app.modules.agent.agent_host.harness.run_config import AgentHostRunConfig
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    ConversationType,
)

REPO = ProjectRepo(owner="octo", repo="widgets", ref="release/v1")


def _conversation(*, repo: ProjectRepo | None, cwd: str | None) -> Conversation:
    return Conversation(
        id=uuid7(),
        pod_id=uuid7(),
        user_id=uuid7(),
        agent_id=uuid7(),
        title="checkout",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
        repo=repo,
        cwd=cwd,
    )


def _spec(conversation: Conversation) -> AgentHostRunSpec:
    harness_id = uuid7()
    return run_spec_from(
        {"agent_run_id": str(uuid7()), "prompt": {"user_prompt": "fix it"}},
        conversation=conversation,
        harness_id=harness_id,
        profile_revision="rev-1",
        run_config=AgentHostRunConfig(
            harness_id=harness_id,
            runtime_profile_id=uuid7(),
            config_selections={},
            wait_timeout_seconds=300,
            model_name=None,
        ),
        system_prompt="instructions",
        system_prompt_delivery=None,
        resume_session_id=None,
        run_deadline=datetime.now(timezone.utc),
    )


def test_a_worktree_conversation_names_its_repository_ref_and_slug() -> None:
    spec = _spec(_conversation(repo=REPO, cwd=REPO.worktree_dir("ab3f2k7q")))

    assert spec.workspace_cwd == REPO.worktree_dir("ab3f2k7q")
    assert spec.repository == AgentHostRunRepository(
        owner="octo", repo="widgets", ref="release/v1", slug="ab3f2k7q"
    )
    assert spec.model_dump(mode="json")["repository"] == {
        "owner": "octo",
        "repo": "widgets",
        "ref": "release/v1",
        "slug": "ab3f2k7q",
    }


def test_a_conversation_with_no_cwd_yet_names_the_worktree_it_will_have() -> None:
    """A run a webhook started has no recorded cwd; the derived one is a
    worktree all the same (``default_worktree_cwd``)."""
    conversation = _conversation(
        repo=ProjectRepo(owner="octo", repo="widgets"), cwd=None
    )
    spec = _spec(conversation)

    assert spec.repository is not None
    assert spec.repository.ref is None
    assert spec.workspace_cwd == REPO.worktree_dir(spec.repository.slug)


def test_nothing_is_named_where_the_directory_is_not_that_worktree() -> None:
    # No repository at all.
    assert _spec(_conversation(repo=None, cwd=None)).repository is None
    # A conversation from before worktrees, working in the clone itself.
    assert _spec(_conversation(repo=REPO, cwd=REPO.clone_dir)).repository is None
    # A directory set by hand.
    assert (
        _spec(
            _conversation(repo=REPO, cwd="/home/user/lemma/c/2026-01-01/x")
        ).repository
        is None
    )
    # Another repository's worktree.
    other = ProjectRepo(owner="octo", repo="gadgets")
    assert (
        _spec(_conversation(repo=REPO, cwd=other.worktree_dir("ab3f2k7q"))).repository
        is None
    )


def test_a_spec_from_before_the_field_still_reads() -> None:
    """Command rows written by a release that predates ``repository``."""
    spec = _spec(_conversation(repo=REPO, cwd=REPO.worktree_dir("ab3f2k7q")))
    wire = spec.model_dump(mode="json")
    del wire["repository"]

    assert AgentHostRunSpec.model_validate(wire).repository is None
