"""Analytics module registration."""

from app.core.registry import LemmaModule


def _event_routers():
    from app.composition import analytics_consumer

    return [analytics_consumer.router]


module = LemmaModule(
    name="analytics",
    event_routers=_event_routers,
    # One consumer group per stream, named for this module so its cursor is
    # independent: analytics falling behind must never slow a consumer that
    # the product depends on.
    stream_groups=(
        ("identity_events", "analytics-identity"),
        ("pod_events", "analytics-pod"),
        ("datastore.events", "analytics-datastore"),
        ("function.events", "analytics-function"),
        ("agent_events", "analytics-agent"),
    ),
)
