"""The routing key a schedule stores and the one a delivery produces are one key.

They are built in two different places -- `_github_binding` when the schedule is
created, from the account and the trigger; and `GitHubWebhookSource.normalize`
when a delivery arrives, from the payload and the header. If they disagree by so
much as the type of the installation id, nothing ever matches and there is no
error anywhere to say so. That is the failure this file exists to prevent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app.composition.schedule_connectors import _github_binding
from app.composition.webhook_sources.github import GitHubWebhookSource
from app.modules.connectors.config import connector_settings
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.webhook_source import WebhookDelivery

SECRET = "binding-test-secret"
INSTALLATION_ID = 158040062


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


async def _delivered_key(event: str, payload: dict) -> dict:
    raw = json.dumps(payload).encode()
    delivery = WebhookDelivery(
        source="github",
        raw_body=raw,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
            "X-GitHub-Event": event,
        },
    )
    source = GitHubWebhookSource()
    normalized = source.normalize(await source.verify(delivery))
    assert normalized is not None and normalized.match is not None
    return normalized.match


@pytest.mark.parametrize(
    "event",
    [
        "push",
        "pull_request",
        "issues",
        "issue_comment",
        "workflow_run",
        "check_suite",
        "release",
    ],
)
async def test_the_stored_key_matches_the_delivered_key(event):
    """Every catalog trigger, against a delivery of the event it names."""
    stored = _github_binding(
        # `external_ref` is a string column; the payload's `installation.id` is
        # a JSON number. This assertion is what keeps them comparable.
        trigger=SimpleNamespace(event_type=event, connector_id="github"),
        account=SimpleNamespace(external_ref=str(INSTALLATION_ID)),
    )
    payloads = {
        "push": {"after": "abc"},
        "pull_request": {"action": "opened", "pull_request": {"id": 1, "head": {}}},
        "issues": {"action": "opened", "issue": {"id": 2}},
        "issue_comment": {"action": "created", "comment": {"id": 3}},
        "workflow_run": {"workflow_run": {"id": 4, "status": "completed"}},
        "check_suite": {"check_suite": {"id": 5, "status": "completed"}},
        "release": {"action": "published", "release": {"id": 6}},
    }
    payload = {
        **payloads[event],
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": 99},
    }

    assert stored == await _delivered_key(event, payload)


async def test_an_unbound_account_is_refused_rather_than_silently_unroutable():
    """A schedule bound to nothing can never fire, so it must not be created.

    An account left over from before the App cutover has no installation, and a
    routing key without one would either match nothing or -- worse, if the key
    were allowed to omit it -- match every organization's events.
    """
    with pytest.raises(ScheduleValidationError):
        _github_binding(
            trigger=SimpleNamespace(event_type="push", connector_id="github"),
            account=SimpleNamespace(external_ref=None),
        )
