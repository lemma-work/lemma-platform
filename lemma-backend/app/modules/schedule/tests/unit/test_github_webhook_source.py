"""GitHub's half of the inbound path: what it accepts, and what it calls it.

One App has one webhook URL, so every event for every organization that
installed it arrives at the same endpoint. Everything here is about telling
them apart safely.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.modules.connectors.infrastructure.webhook_sources.github import (
    GitHubWebhookSource,
    source_event_id,
)
from app.modules.connectors.config import connector_settings
from app.modules.schedule.contracts.webhook_source import (
    WebhookDelivery,
    WebhookNotVerified,
)

SECRET = "a-webhook-secret"


def _delivery(payload: dict, *, event: str, secret: str = SECRET) -> WebhookDelivery:
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return WebhookDelivery(
        source="github",
        raw_body=raw,
        headers={
            # Deliberately the casing GitHub actually sends, not lowercase:
            # header lookup has to be case-insensitive or nothing verifies.
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": "72d3162e-cc78-11e3-81ab-4c9367dc0958",
        },
    )


def _pull_request(action: str = "opened", head_sha: str = "abc123") -> dict:
    return {
        "action": action,
        "number": 42,
        "pull_request": {"id": 279147437, "head": {"sha": head_sha}},
        "repository": {"id": 1296269, "name": "api", "owner": {"login": "octo"}},
        "installation": {"id": 158040062},
    }


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(
        connector_settings, "connector_github_app_webhook_secret", SECRET
    )
    monkeypatch.setattr(
        connector_settings,
        "connector_github_app_webhook_secret_previous",
        None,
        raising=False,
    )


class TestVerification:
    async def test_a_signed_delivery_is_accepted(self):
        verified = await GitHubWebhookSource().verify(
            _delivery(_pull_request(), event="pull_request")
        )
        assert verified.payload["number"] == 42

    async def test_an_unsigned_or_wrongly_signed_delivery_is_refused(self):
        source = GitHubWebhookSource()
        with pytest.raises(WebhookNotVerified):
            await source.verify(
                _delivery(_pull_request(), event="pull_request", secret="wrong")
            )

        unsigned = WebhookDelivery(
            source="github", raw_body=b"{}", headers={"X-GitHub-Event": "push"}
        )
        with pytest.raises(WebhookNotVerified):
            await source.verify(unsigned)

    async def test_a_tampered_body_is_refused(self):
        delivery = _delivery(_pull_request(), event="pull_request")
        tampered = WebhookDelivery(
            source="github",
            raw_body=delivery.raw_body.replace(b'"number": 42', b'"number": 43'),
            headers=delivery.headers,
        )
        with pytest.raises(WebhookNotVerified):
            await GitHubWebhookSource().verify(tampered)

    async def test_no_configured_secret_refuses_rather_than_accepts(self, monkeypatch):
        """Unconfigured is not the same as unauthenticated.

        An endpoint that accepts everything because no secret is set looks
        exactly like one that is working.
        """
        monkeypatch.setattr(
            connector_settings, "connector_github_app_webhook_secret", None
        )
        with pytest.raises(WebhookNotVerified):
            await GitHubWebhookSource().verify(
                _delivery(_pull_request(), event="pull_request")
            )

    async def test_the_previous_secret_still_verifies_during_a_rotation(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            connector_settings, "connector_github_app_webhook_secret", "the-new-one"
        )
        monkeypatch.setattr(
            connector_settings,
            "connector_github_app_webhook_secret_previous",
            SECRET,
            raising=False,
        )
        verified = await GitHubWebhookSource().verify(
            _delivery(_pull_request(), event="pull_request", secret=SECRET)
        )
        assert verified.payload["action"] == "opened"


class TestRouting:
    async def _normalized(self, payload: dict, event: str):
        source = GitHubWebhookSource()
        return source.normalize(await source.verify(_delivery(payload, event=event)))

    async def test_the_routing_key_is_tenant_scoped(self):
        normalized = await self._normalized(_pull_request(), "pull_request")
        assert normalized is not None
        assert normalized.match == {
            "source": "github",
            "installation_id": "158040062",
            "event": "pull_request",
        }

    async def test_a_delivery_with_no_installation_is_dropped(self):
        """Without a tenant, the key would route one org's events to another's."""
        payload = _pull_request()
        payload.pop("installation")
        assert await self._normalized(payload, "pull_request") is None

    async def test_an_unsubscribed_event_is_acknowledged_and_dropped(self):
        """An App subscribes at the App level, so unwanted events do arrive.

        Answering non-2xx to those would have GitHub disable the hook for the
        events that matter.
        """
        assert await self._normalized({"installation": {"id": 1}}, "star") is None

    async def test_only_the_declared_repository_matches(self):
        normalized = await self._normalized(_pull_request(), "pull_request")
        assert normalized is not None and normalized.refine is not None
        assert normalized.refine({"repository_id": 1296269})
        assert normalized.refine({"repository_id": "1296269"})
        assert not normalized.refine({"repository_id": 999})
        # Declaring nothing means every repository in the installation.
        assert normalized.refine({})

    async def test_only_the_declared_actions_match(self):
        normalized = await self._normalized(_pull_request("opened"), "pull_request")
        assert normalized is not None and normalized.refine is not None
        assert normalized.refine({"actions": ["opened", "reopened"]})
        assert not normalized.refine({"actions": ["closed"]})
        # An empty list is "no opinion", not "nothing".
        assert normalized.refine({"actions": []})


class TestSourceEventId:
    """The id is per-*event*. `X-GitHub-Delivery` is per-*delivery*.

    Redelivering -- from the App's advanced tab, or by GitHub's own retry --
    issues a new delivery id for something that happened once, so using it would
    run every matched schedule a second time.
    """

    async def test_a_redelivery_collapses_onto_the_first_attempt(self):
        source = GitHubWebhookSource()
        payload = _pull_request()
        first = _delivery(payload, event="pull_request")
        second = WebhookDelivery(
            source="github",
            raw_body=first.raw_body,
            headers={**first.headers, "X-GitHub-Delivery": "a-different-delivery-id"},
        )
        ids = [
            source.normalize(await source.verify(d)).source_event_id
            for d in (first, second)
        ]
        assert ids[0] == ids[1]

    def test_a_new_push_to_the_same_ref_is_a_new_event(self):
        base = {"after": "sha-one"}
        assert source_event_id("push", "1", 2, base) != source_event_id(
            "push", "1", 2, {"after": "sha-two"}
        )

    def test_the_same_event_in_two_installations_is_two_events(self):
        payload = _pull_request()
        assert source_event_id("pull_request", "1", 2, payload) != source_event_id(
            "pull_request", "9", 2, payload
        )

    def test_a_new_commit_on_a_pull_request_is_a_new_event(self):
        assert source_event_id(
            "pull_request", "1", 2, _pull_request(head_sha="aaa")
        ) != source_event_id("pull_request", "1", 2, _pull_request(head_sha="bbb"))

    def test_reopening_is_not_the_same_event_as_opening(self):
        assert source_event_id(
            "pull_request", "1", 2, _pull_request("opened")
        ) != source_event_id("pull_request", "1", 2, _pull_request("reopened"))

    def test_a_run_that_advances_status_is_a_new_event(self):
        def run(status: str) -> dict:
            return {"workflow_run": {"id": 30433642, "status": status}}

        assert source_event_id(
            "workflow_run", "1", 2, run("in_progress")
        ) != source_event_id("workflow_run", "1", 2, run("completed"))

    def test_an_event_missing_its_key_produces_none(self):
        """Better no id than an unstable one: a missing id is refused loudly by
        the handler, while an id derived from `None` would silently collapse
        every such delivery onto one."""
        assert source_event_id("push", "1", 2, {}) is None
        assert source_event_id("unknown_event", "1", 2, {"after": "x"}) is None


# What a pod reviewing pull requests reacts to: a person's review, a comment on
# one line of the diff, and one CI job finishing. The payloads below follow the
# shapes in GitHub's webhook documentation, trimmed to what a reader needs.

REPOSITORY = {
    "id": 1296269,
    "name": "api",
    "full_name": "octo/api",
    "owner": {"login": "octo", "id": 583231, "type": "Organization"},
}
INSTALLATION = {"id": 158040062, "node_id": "MDIzOkludGVncmF0aW9uSW5zdGFsbGF0aW9uMQ=="}
SENDER = {"login": "hubot", "id": 2, "type": "User"}
HEAD_SHA = "ec26c3e57ca3a959ca5aad62de7213c562f8c821"


def _pull_request_object(head_ref: str = "feature/commas") -> dict:
    return {
        "id": 279147437,
        "number": 42,
        "state": "open",
        "title": "Accept trailing commas",
        "head": {"ref": head_ref, "sha": HEAD_SHA, "repo": REPOSITORY},
        "base": {"ref": "main", "sha": "f95f852bd8fca8fcc58a9a2d6c842781e32a215e"},
    }


def _review(
    action: str = "submitted", *, state: str = "approved", body: str = "Ship it."
) -> dict:
    return {
        "action": action,
        "review": {
            "id": 2041503473,
            "node_id": "PRR_kwDOABPHjc55rqvx",
            "user": SENDER,
            "body": body,
            "commit_id": HEAD_SHA,
            "submitted_at": "2024-07-15T09:21:44Z",
            "state": state,
            "author_association": "MEMBER",
        },
        "pull_request": _pull_request_object(),
        "repository": REPOSITORY,
        "installation": INSTALLATION,
        "sender": SENDER,
    }


def _review_comment(
    action: str = "created",
    *,
    updated_at: str = "2024-07-15T09:21:44Z",
    body: str = "This drops the last field when the line ends in a comma.",
) -> dict:
    payload = {
        "action": action,
        "comment": {
            "id": 2006215484,
            "node_id": "PRRC_kwDOABPHjc53lPU8",
            "pull_request_review_id": 2041503473,
            "diff_hunk": "@@ -16,6 +16,8 @@ def parse(line):",
            "path": "app/parse.py",
            "commit_id": HEAD_SHA,
            "original_commit_id": HEAD_SHA,
            "user": SENDER,
            "body": body,
            "created_at": "2024-07-15T09:21:44Z",
            "updated_at": updated_at,
            "line": 18,
            "side": "RIGHT",
            "author_association": "MEMBER",
        },
        "pull_request": _pull_request_object(),
        "repository": REPOSITORY,
        "installation": INSTALLATION,
        "sender": SENDER,
    }
    if action == "edited":
        payload["changes"] = {"body": {"from": "Drops the last field."}}
    return payload


def _check_run(
    action: str = "completed",
    *,
    status: str = "completed",
    conclusion: str | None = "failure",
    head_branch: str | None = "feature/commas",
    pull_requests: list[dict] | None = None,
    requested_action: str | None = None,
) -> dict:
    if pull_requests is None:
        pull_requests = [
            {
                "id": 279147437,
                "number": 42,
                "head": {"ref": "feature/commas", "sha": HEAD_SHA, "repo": REPOSITORY},
                "base": {"ref": "main", "sha": HEAD_SHA, "repo": REPOSITORY},
            }
        ]
    payload = {
        "action": action,
        "check_run": {
            "id": 128620228,
            "node_id": "MDg6Q2hlY2tSdW4xMjg2MjAyMjg=",
            "name": "test (3.14)",
            "head_sha": HEAD_SHA,
            "external_id": "",
            "status": status,
            "conclusion": conclusion,
            "started_at": "2024-07-15T09:20:02Z",
            "completed_at": "2024-07-15T09:21:40Z" if status == "completed" else None,
            "output": {"title": "2 failed", "summary": "", "annotations_count": 2},
            "check_suite": {
                "id": 118578147,
                "head_branch": head_branch,
                "head_sha": HEAD_SHA,
                "status": status,
                "conclusion": conclusion,
            },
            "app": {"id": 15368, "slug": "github-actions", "name": "GitHub Actions"},
            "pull_requests": pull_requests,
        },
        "repository": REPOSITORY,
        "installation": INSTALLATION,
        "sender": SENDER,
    }
    if requested_action is not None:
        payload["requested_action"] = {"identifier": requested_action}
    return payload


NEW_EVENTS = {
    "pull_request_review": _review,
    "pull_request_review_comment": _review_comment,
    "check_run": _check_run,
}


async def _normalize(payload: dict, event: str, *, delivery_id: str | None = None):
    source = GitHubWebhookSource()
    delivery = _delivery(payload, event=event)
    if delivery_id is not None:
        delivery = WebhookDelivery(
            source="github",
            raw_body=delivery.raw_body,
            headers={**delivery.headers, "X-GitHub-Delivery": delivery_id},
        )
    normalized = source.normalize(await source.verify(delivery))
    assert normalized is not None, f"{event} was dropped"
    return normalized


@pytest.mark.parametrize("event", sorted(NEW_EVENTS))
class TestReviewAndCheckEventsAreDelivered:
    """Before these were supported they were acknowledged and dropped, so a
    schedule on any of them could be created and would never fire."""

    async def test_the_event_routes_on_its_own_tenant_scoped_key(self, event):
        normalized = await _normalize(NEW_EVENTS[event](), event)
        assert normalized.match == {
            "source": "github",
            "installation_id": "158040062",
            "event": event,
        }

    async def test_a_redelivery_collapses_onto_the_first_attempt(self, event):
        payload = NEW_EVENTS[event]()
        first = await _normalize(payload, event, delivery_id="delivery-one")
        again = await _normalize(payload, event, delivery_id="delivery-two")
        assert first.source_event_id == again.source_event_id

    async def test_the_agent_wakes_on_the_branch_under_review(self, event):
        normalized = await _normalize(NEW_EVENTS[event](), event)
        assert normalized.context == {
            "repo": {"owner": "octo", "repo": "api", "ref": "feature/commas"}
        }

    async def test_only_the_declared_repository_matches(self, event):
        normalized = await _normalize(NEW_EVENTS[event](), event)
        assert normalized.refine is not None
        assert normalized.refine({"repository_id": 1296269})
        assert not normalized.refine({"repository_id": 999})


class TestPullRequestReviews:
    def _id(self, payload: dict) -> str | None:
        return source_event_id("pull_request_review", "1", 2, payload)

    async def test_only_the_declared_review_actions_match(self):
        normalized = await _normalize(_review("submitted"), "pull_request_review")
        assert normalized.refine is not None
        assert normalized.refine({"actions": ["submitted"]})
        assert not normalized.refine({"actions": ["edited", "dismissed"]})
        assert normalized.refine({"actions": []})

    async def test_a_dismissal_fires_only_where_dismissals_were_asked_for(self):
        normalized = await _normalize(
            _review("dismissed", state="dismissed"), "pull_request_review"
        )
        assert normalized.refine is not None
        assert normalized.refine({"actions": ["dismissed"]})
        assert not normalized.refine({"actions": ["submitted"]})

    def test_submitting_and_dismissing_one_review_are_two_events(self):
        assert self._id(_review("submitted")) != self._id(
            _review("dismissed", state="dismissed")
        )

    def test_each_edit_of_a_review_is_its_own_event(self):
        """A review has no `updated_at`; only the body says which edit this is.
        Keyed on id and action alone, a second edit would never fire."""
        first = self._id(_review("edited", body="Ship it once CI is green."))
        second = self._id(_review("edited", body="Ship it -- CI is green."))
        assert first != second

    def test_two_reviews_on_one_pull_request_are_two_events(self):
        other = _review(state="changes_requested")
        other["review"]["id"] = 2041503999
        assert self._id(_review()) != self._id(other)


class TestPullRequestReviewComments:
    def _id(self, payload: dict) -> str | None:
        return source_event_id("pull_request_review_comment", "1", 2, payload)

    async def test_only_the_declared_comment_actions_match(self):
        normalized = await _normalize(
            _review_comment("created"), "pull_request_review_comment"
        )
        assert normalized.refine is not None
        assert normalized.refine({"actions": ["created"]})
        assert not normalized.refine({"actions": ["edited", "deleted"]})

    def test_each_edit_of_a_comment_is_its_own_event(self):
        """`updated_at` moves on every edit; a redelivery carries the same one."""
        first = self._id(_review_comment("edited", updated_at="2024-07-15T09:30:00Z"))
        second = self._id(_review_comment("edited", updated_at="2024-07-15T09:45:00Z"))
        assert first != second

    def test_creating_and_deleting_a_comment_are_two_events(self):
        assert self._id(_review_comment("created")) != self._id(
            _review_comment("deleted")
        )


class TestCheckRuns:
    def _id(self, payload: dict) -> str | None:
        return source_event_id("check_run", "1", 2, payload)

    async def test_only_the_declared_check_run_actions_match(self):
        normalized = await _normalize(_check_run("completed"), "check_run")
        assert normalized.refine is not None
        assert normalized.refine({"actions": ["completed"]})
        assert not normalized.refine({"actions": ["created"]})

    def test_creating_and_completing_a_run_are_two_events(self):
        created = _check_run("created", status="queued", conclusion=None)
        assert self._id(created) != self._id(_check_run("completed"))

    def test_a_run_completed_again_with_a_new_conclusion_is_a_new_event(self):
        """An App can rewrite a finished run's conclusion; GitHub reports that
        as another `completed`, and the new verdict is worth reacting to."""
        assert self._id(_check_run(conclusion="failure")) != self._id(
            _check_run(conclusion="success")
        )

    def test_a_rerun_request_is_not_the_completion_it_follows(self):
        """`rerequested` arrives for a run that has already completed, with the
        same status and conclusion as the delivery before it."""
        assert self._id(_check_run("rerequested")) != self._id(_check_run("completed"))

    def test_two_buttons_on_one_run_are_two_events(self):
        assert self._id(
            _check_run("requested_action", requested_action="fix-lint")
        ) != self._id(_check_run("requested_action", requested_action="ignore"))

    async def test_a_run_with_no_suite_branch_binds_its_pull_requests_head(self):
        payload = _check_run(head_branch=None)
        payload["check_run"]["pull_requests"][0]["head"]["ref"] = "feature/from-pr"
        normalized = await _normalize(payload, "check_run")
        assert normalized.context["repo"]["ref"] == "feature/from-pr"

    async def test_a_run_on_a_forks_push_binds_no_branch(self):
        """GitHub documents a fork's push as a null `head_branch` and no pull
        requests. A guessed branch would fail the clone outright; no branch
        leaves the agent on the default one."""
        normalized = await _normalize(
            _check_run(head_branch=None, pull_requests=[]), "check_run"
        )
        assert normalized.context == {"repo": {"owner": "octo", "repo": "api"}}
