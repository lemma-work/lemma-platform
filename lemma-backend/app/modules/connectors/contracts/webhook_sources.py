"""The webhook sources this deployment accepts, as `schedule` mounts them.

One factory, and it is the same exception to "operations, not classes" that
`agent/contracts/workflow_control.py` is: `WebhookSourcePlugin` is a port with
four members that `POST /webhooks/{source}` holds for the length of a delivery,
so publishing the plugins' methods as free functions would only make the caller
reassemble them.

Here rather than in `schedule` because the plugins are made of connectors: the
Composio one verifies through Composio's SDK, the GitHub one through the App's
webhook secret, and both secrets belong to a connector account. `schedule` owns
the endpoint, the registry type and the routing; `connectors` owns what a
provider's delivery is and how it proves itself.

The plugin classes stay unpublished. The caller names `WebhookSourceRegistry`,
which is `schedule`'s own type, and cannot reach past it to an SDK client.
"""

from __future__ import annotations

from app.modules.schedule.contracts import WebhookSourceRegistry


def default_webhook_sources() -> WebhookSourceRegistry:
    """Every source with a plugin. A source with none is refused by the registry.

    Imported inside the call, and measured: the Composio plugin reaches the
    Composio SDK client, which is the single heaviest import in this module.
    The registry is built once behind an `lru_cache` at its call site, so this
    runs at most once per process and never on the delivery path.
    """
    from app.modules.connectors.infrastructure.webhook_sources.composio import (
        ComposioWebhookSource,
    )
    from app.modules.connectors.infrastructure.webhook_sources.github import (
        GitHubWebhookSource,
    )

    return WebhookSourceRegistry([ComposioWebhookSource(), GitHubWebhookSource()])


__all__ = ["default_webhook_sources"]
