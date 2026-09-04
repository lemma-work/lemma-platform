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
