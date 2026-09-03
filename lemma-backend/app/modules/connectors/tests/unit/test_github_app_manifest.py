"""The checked-in manifest has to be one GitHub will actually accept.

It exists so a new environment is a copy rather than a memory exercise, which
is worth nothing if GitHub rejects it. It did: `installation` and
`installation_repositories` in `default_events` failed with

    Default events unsupported: installation and installation_repositories
    Default events are not supported by permissions: installation and
    installation_repositories

and the App could not be created at all. Those two are delivered to every App
whether or not it asks -- observed before this was known, when
`installation.new_permissions_accepted` arrived twice while the App's `events`
list contained neither -- so subscribing to them is not merely unnecessary, it
is invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition.webhook_sources.github import SUPPORTED_EVENTS

pytestmark = pytest.mark.unit

MANIFEST = Path(__file__).resolve().parents[5] / "config" / "github-app-manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_it_subscribes_to_exactly_the_events_with_triggers():
    """No more, because GitHub rejects unsubscribable ones; no fewer, because a
    trigger nobody subscribed to never fires and says nothing about why."""
    assert sorted(_manifest()["default_events"]) == sorted(SUPPORTED_EVENTS)


def test_it_asks_for_no_installation_event():
    """The specific rejection, named so a future edit reads the reason."""
    events = _manifest()["default_events"]
    assert not [event for event in events if event.startswith("installation")]


def test_it_leaves_the_per_environment_urls_out():
    """Webhook and callback URLs differ per environment and are filled in when
    the App is created. A value baked in here would be silently wrong for every
    environment but one."""
    manifest = _manifest()
    assert "url" not in manifest.get("hook_attributes", {})
    assert "callback_urls" not in manifest
    assert "redirect_url" not in manifest


def test_it_requests_oauth_during_install():
    """This is what makes the install redirect carry `code` alongside
    `installation_id`; without it the connect flow gets no user token."""
    assert _manifest()["request_oauth_on_install"] is True
