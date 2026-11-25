"""Firing WEBHOOK schedules on an event another module heard on its own ingress.

Not every connector's events arrive at `POST /webhooks/{source}`. A Slack app
has one event URL, and it is the agent surfaces' endpoint: that is where the
pod's bot hears its channels, where the app's signing secret is checked, and
where each message becomes a line of the channel it was said in. A second
Slack endpoint for schedules would need every app's manifest pointed at it and
would verify the same signature a second time. So the surfaces module, having
heard and logged a message, hands it here as a `NormalizedWebhook` -- routing
key, event id, payload -- and states the pods it is for.

The pods are the tenancy. A Slack channel id is something any author can type
into a schedule's config, and matching is containment across every tenant's
schedules; what makes a channel's messages a pod's is that the pod connected
the channel to its bot. `fire_heard_event` refuses a call that does not say.

A submodule for the same reason as `dispatch.py` beside it: this reaches the
repositories, and `contracts/__init__` is imported by anything that wants any
contract at all.
"""

from __future__ import annotations

from app.modules.schedule.contracts.webhook_source import NormalizedWebhook
from app.modules.schedule.services.webhook_handler import fire_heard_event

__all__ = ["NormalizedWebhook", "fire_heard_event"]
