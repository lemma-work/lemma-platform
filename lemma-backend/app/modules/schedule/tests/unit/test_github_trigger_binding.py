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

import pytest

from app.modules.connectors.contracts.triggers import TriggerBinding
from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
    _github_binding,
    defaults_the_author_left_out,
)
from app.composition.webhook_sources.github import GitHubWebhookSource
from app.modules.connectors.config import connector_settings
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.contracts.webhook_source import WebhookDelivery

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
        TriggerBinding(
            connector_id="github",
            event_type=event,
            # `external_ref` is a string column; the payload's `installation.id`
            # is a JSON number. This assertion is what keeps them comparable.
            installation_id=str(INSTALLATION_ID),
        )
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
            TriggerBinding(
                connector_id="github", event_type="push", installation_id=None
            )
        )


class TestDeclaredDefaults:
    """A `default` in a trigger's `config_schema` has to apply server-side.

    Otherwise it is decoration: the form prefills it and an API- or CLI-created
    schedule gets nothing. `workflow_run` is why that matters -- a busy
    repository emits one delivery per run per state change, so the API path
    would wake an agent three times for one CI run while the UI path woke it
    once.

    Reading the defaults out of the schema belongs to `connectors`, which owns
    `config_schema`, and is covered there. What is decided here is the half that
    is a schedule policy: whose value wins.
    """

    @staticmethod
    def _binding(**defaults) -> TriggerBinding:
        return TriggerBinding(
            connector_id="github", event_type="workflow_run", config_defaults=defaults
        )

    def test_a_default_fills_a_key_the_author_left_out(self):
        binding = self._binding(actions=["completed"])

        assert defaults_the_author_left_out(binding, {}) == {"actions": ["completed"]}
        assert defaults_the_author_left_out(binding, None) == {"actions": ["completed"]}

    def test_what_the_author_wrote_is_never_overwritten(self):
        binding = self._binding(actions=["completed"])

        assert defaults_the_author_left_out(binding, {"actions": ["requested"]}) == {}
        # An empty list is a decision, not an absence.
        assert defaults_the_author_left_out(binding, {"actions": []}) == {}

    def test_a_trigger_that_declares_nothing_contributes_nothing(self):
        assert defaults_the_author_left_out(self._binding(), {}) == {}
