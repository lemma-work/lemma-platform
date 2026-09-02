"""The webhook sources this deployment accepts.

Assembled here rather than in the schedule module because the plugins reach
across into connectors -- a Composio SDK call, a GitHub App's webhook secret --
and `app/composition` is where a module is allowed to depend on another one.
"""

from __future__ import annotations

from app.modules.schedule.domain.webhook_source import WebhookSourceRegistry


def default_webhook_sources() -> WebhookSourceRegistry:
    from app.composition.webhook_sources.composio import ComposioWebhookSource
    from app.composition.webhook_sources.github import GitHubWebhookSource

    return WebhookSourceRegistry([ComposioWebhookSource(), GitHubWebhookSource()])


__all__ = ["default_webhook_sources"]
