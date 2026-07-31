"""Schedule module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.schedule.api.controllers.schedule_controller import (
        router as schedule,
    )
    from app.modules.schedule.api.controllers.webhook_controller import (
        router as webhook,
    )

    return [schedule, webhook]


def _event_routers():
    # schedule_consumer also defines the `handle_llm_filter_task` streaq task,
    # which registers on import here (no separate register_streaq needed).
    from app.modules.schedule.handlers import (
        datastore_consumer,
        pod_lifecycle_consumer,
        schedule_consumer,
        schedule_notification_consumer,
    )

    return [
        schedule_consumer.router,
        datastore_consumer.router,
        pod_lifecycle_consumer.router,
        schedule_notification_consumer.router,
    ]


module = LemmaModule(
    name="schedule",
    routers=_routers,
    event_routers=_event_routers,
    stream_groups=(
        ("schedule_events", "schedule-notifications"),
        ("datastore.events", "schedule-datastore-events"),
        ("pod_events", "schedule-pod-events"),
    ),
)
