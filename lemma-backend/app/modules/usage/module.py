"""Usage module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.usage.api.controllers import router as usage

    return [usage]


def _event_routers():
    from app.modules.usage.handlers import limit_notification_consumer

    return [limit_notification_consumer.router]


module = LemmaModule(
    name="usage",
    routers=_routers,
    event_routers=_event_routers,
    # The module's first consumer, of the stream it has always published to.
    # `usage_events` carried two event types from the day it existed and nothing
    # subscribed to either, so a spend limit's only signal was the refusal.
    #
    # Its own group, named for this module: a consumer that falls behind must
    # never hold up one the product depends on. The registration is also what
    # creates the group -- a stream written to before its group exists drops
    # everything published in between.
    stream_groups=(("usage_events", "usage-notifications"),),
)
