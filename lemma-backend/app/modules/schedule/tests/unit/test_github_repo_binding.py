"""A triggered agent wakes up standing in the repository the event came from.

This is what separates "webhooks that start a chat" from a GitHub integration.
The delivery says which repository and which branch; the schedule says which
connected account to clone as; `parse_project_repo` downstream turns the pair
into a checkout the agent's first `gh pr diff` runs inside.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.webhook_sources.github import _repo_context
from app.modules.agent.services.workspace_location import parse_project_repo
from app.modules.workflow.services.schedule_start_service import (
    _conversation_metadata,
)

pytestmark = pytest.mark.unit

REPOSITORY = {"id": 1296269, "name": "api", "owner": {"login": "octo"}}


class TestWhatTheDeliverySays:
    def test_a_pull_request_binds_to_its_head_branch(self):
        """An agent asked to review a pull request and dropped on `main` is
        looking at the wrong code."""
        context = _repo_context(
            {
                "repository": REPOSITORY,
                "pull_request": {"head": {"ref": "feature/commas"}},
            },
            "pull_request",
        )
        assert context == {
            "repo": {"owner": "octo", "repo": "api", "ref": "feature/commas"}
        }

    def test_a_push_binds_to_the_branch_it_pushed(self):
        context = _repo_context(
            {"repository": REPOSITORY, "ref": "refs/heads/topic"}, "push"
        )
        assert context["repo"]["ref"] == "topic"

    def test_a_tag_push_names_no_branch(self):
        """`git clone --branch refs/tags/v1` is not what was meant."""
        context = _repo_context(
            {"repository": REPOSITORY, "ref": "refs/tags/v1.0.0"}, "push"
        )
        assert "ref" not in context["repo"]

    def test_other_events_leave_the_default_branch_to_the_clone(self):
        context = _repo_context(
            {"repository": REPOSITORY, "issue": {"id": 1}}, "issues"
        )
        assert context == {"repo": {"owner": "octo", "repo": "api"}}

    def test_a_delivery_with_no_repository_binds_nothing(self):
        assert _repo_context({"installation": {"id": 1}}, "push") == {}


class TestWhatTheScheduleAdds:
    def test_the_schedules_account_becomes_the_clone_identity(self):
        """Deliberately the person's account, not the App installation.

        The sandbox's `git` and `gh` act as the user, so what an agent pushes is
        attributed to whoever owns the repository rather than to a bot.
        """
        account_id = uuid4()
        metadata = _conversation_metadata(
            SimpleNamespace(account_id=account_id),
            {"repo": {"owner": "octo", "repo": "api", "ref": "topic"}},
        )
        assert metadata == {
            "repo": {
                "owner": "octo",
                "repo": "api",
                "ref": "topic",
                "account_id": str(account_id),
            }
        }

    def test_a_schedule_with_no_account_still_binds_the_repository(self):
        """The credential bridge falls back to resolving the user's account."""
        metadata = _conversation_metadata(
            SimpleNamespace(account_id=None), {"repo": {"owner": "octo", "repo": "api"}}
        )
        assert metadata == {"repo": {"owner": "octo", "repo": "api"}}

    def test_a_firing_that_names_no_repository_sets_no_metadata(self):
        for value in ({}, None, {"repo": {}}, {"repo": "octo/api"}):
            assert (
                _conversation_metadata(SimpleNamespace(account_id=uuid4()), value)
                is None
            )


class TestTheTwoHalvesAgree:
    def test_what_the_plugin_produces_is_what_the_agent_can_parse(self):
        """The two ends of this contract are written in different modules.

        `_repo_context` builds the dict; `parse_project_repo` reads it, from
        untrusted metadata, dropping anything malformed *silently* -- so a
        mismatch here would not raise, it would quietly put the agent in an
        empty scratchpad.
        """
        account_id = uuid4()
        context = _repo_context(
            {
                "repository": REPOSITORY,
                "pull_request": {"head": {"ref": "feature/commas"}},
            },
            "pull_request",
        )
        metadata = _conversation_metadata(
            SimpleNamespace(account_id=account_id), context
        )
        assert metadata is not None

        parsed = parse_project_repo(metadata["repo"])
        assert parsed is not None
        assert parsed.full_name == "octo/api"
        assert parsed.ref == "feature/commas"
        assert parsed.account_id == account_id
        assert parsed.cwd.endswith("/octo/api")
