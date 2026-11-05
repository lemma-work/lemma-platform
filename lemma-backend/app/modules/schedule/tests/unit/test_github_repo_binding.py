"""A triggered agent wakes up standing in the repository the event came from.

This is what separates "webhooks that start a chat" from a GitHub integration.
The delivery says which repository and which branch; `parse_project_repo`
downstream turns that into a checkout the agent's first `gh pr diff` runs
inside. Which account it clones as is the pod's `github` connector's decision,
for the person the run acts for -- never an account the run's metadata names.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.webhook_sources.github import _repo_context
from app.modules.agent.domain.workspace_location import parse_project_repo
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
                "pull_request": {"head": {"ref": "feature/commas", "repo": REPOSITORY}},
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


def _schedule(*, pod_connector_slug: str | None = None, name: str | None = None):
    """The fields `_conversation_metadata` reads off a schedule row."""
    return SimpleNamespace(
        id=uuid4(),
        pod_connector_slug=pod_connector_slug,
        name=name,
        schedule_type="TIME",
    )


class TestWhatTheScheduleAdds:
    def test_the_repository_is_bound_and_no_account_rides_along(self):
        """The clone identity is not the schedule's to name: the credential
        bridge resolves the pod's `github` connector for whoever the run acts
        for, so an account id in the metadata would be one more thing to trust.
        """
        metadata = _conversation_metadata(
            _schedule(pod_connector_slug="github"),
            {"repo": {"owner": "octo", "repo": "api", "ref": "topic"}},
        )
        assert metadata["repo"] == {"owner": "octo", "repo": "api", "ref": "topic"}

    def test_a_firing_that_names_no_repository_binds_no_repository(self):
        """No repo key at all -- but the metadata itself is no longer empty.

        Every schedule-started run is stamped with where it came from, because
        that is the only way the agent can tell that nobody is waiting on it.
        """
        for value in ({}, None, {"repo": {}}, {"repo": "octo/api"}):
            metadata = _conversation_metadata(
                _schedule(pod_connector_slug="github"), value
            )
            assert "repo" not in metadata

    def test_every_schedule_started_run_says_a_schedule_started_it(self):
        schedule = _schedule(name="daily-invoices")
        metadata = _conversation_metadata(schedule, None)
        assert metadata["started_by"] == "SCHEDULE"
        assert metadata["schedule_id"] == str(schedule.id)
        assert metadata["schedule_name"] == "daily-invoices"
        assert metadata["schedule_type"] == "TIME"


class TestTheTwoHalvesAgree:
    def test_what_the_plugin_produces_is_what_the_agent_can_parse(self):
        """The two ends of this contract are written in different modules.

        `_repo_context` builds the dict; `parse_project_repo` reads it, from
        untrusted metadata, dropping anything malformed *silently* -- so a
        mismatch here would not raise, it would quietly put the agent in an
        empty scratchpad.
        """
        context = _repo_context(
            {
                "repository": REPOSITORY,
                "pull_request": {"head": {"ref": "feature/commas", "repo": REPOSITORY}},
            },
            "pull_request",
        )
        metadata = _conversation_metadata(
            _schedule(pod_connector_slug="github"), context
        )
        assert metadata is not None

        parsed = parse_project_repo(metadata["repo"])
        assert parsed is not None
        assert parsed.full_name == "octo/api"
        assert parsed.ref == "feature/commas"
        assert parsed.cwd.endswith("/octo/api")
