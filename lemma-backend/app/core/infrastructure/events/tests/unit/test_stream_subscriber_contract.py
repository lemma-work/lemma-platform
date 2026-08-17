"""Every stream subscriber must take ``dict`` and filter on ``event_type``.

A Redis Stream has no server-side type filter. Several streams carry more than
one event type — ``surface_events`` carries the webhook plus two analytics
projections, ``schedule_events`` carries fires plus the whole schedule
lifecycle — so every consumer group receives all of them and must sort them out
itself.

Annotating the handler's event parameter with a concrete model instead hands
that job to fast-depends, which validates *before* the handler body and
therefore before the acknowledgement. A message that cannot validate can never
be acked, so it stays in the pending-entries list and the companion reclaim
subscriber (XAUTOCLAIM, 60s idle) hands it back forever.

This is a ratchet, not a style rule. It has already happened once:
``handle_surface_webhook`` annotated ``SurfaceWebhookReceivedEvent`` and turned
every ``surface.connected`` event into a permanent redelivery loop — ~119 an
hour, growing by one stuck message per agent created, because every agent is
given an auto-provisioned Resend mailbox whose creation publishes that event.

The reasoning was already written down, in a comment on the correct handler 80
lines below the broken one, and that did not prevent it. Hence a gate.
"""

from __future__ import annotations

import pytest

from app.core.infrastructure.events import stream_subscriber as ss
from app.core.registry.assembly import import_module_tasks
from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit

#: The only annotation that lets a handler see every event on its stream.
ALLOWED_ANNOTATIONS = {"dict", "dict[str, Any]", "dict[str, object]"}


def _offenders(
    bindings: list[ss.SubscriberEventBinding],
) -> list[str]:
    return [
        f"{binding.handler} on {binding.stream!r}/{binding.group!r} "
        f"declares `event: {binding.annotation}`"
        for binding in bindings
        if binding.annotation not in ALLOWED_ANNOTATIONS
    ]


@pytest.fixture(scope="module")
def registered_bindings() -> list[ss.SubscriberEventBinding]:
    """Import every module's handlers so the decorators have all run.

    ``import_module_tasks`` is the thunk-runner that needs no broker, which is
    what makes this a unit test rather than something that has to stand up
    FastStream.
    """
    import_module_tasks(OSS_MODULES)
    return ss.registered_subscriber_event_bindings()


def test_the_gate_sees_the_real_subscriber_population(registered_bindings):
    """A gate that matches nothing passes forever."""
    assert len(registered_bindings) >= 25, (
        "expected the full stream-subscriber population; only "
        f"{len(registered_bindings)} registered. Did the registry stop importing "
        "handler modules, or did the decorator stop recording?"
    )


def test_every_stream_subscriber_takes_an_untyped_event(registered_bindings):
    offenders = _offenders(registered_bindings)

    assert not offenders, (
        "These subscribers validate before acknowledging, so any other event on "
        "their stream becomes a poison message that is redelivered forever:\n  "
        + "\n  ".join(offenders)
        + "\n\nTake `event: dict`, return early unless `event.get(\"event_type\")` "
        "matches, then `model_validate` inside the handler."
    )


def test_the_gate_actually_fails_on_a_typed_subscriber():
    """Prove the check bites, so a silent pass can never be mistaken for health."""
    typed = ss.SubscriberEventBinding(
        stream="surface_events",
        group="surface-webhook-events",
        handler="app.example.handle_surface_webhook",
        annotation="SurfaceWebhookReceivedEvent",
    )

    assert _offenders([typed]) == [
        "app.example.handle_surface_webhook on 'surface_events'/"
        "'surface-webhook-events' declares `event: SurfaceWebhookReceivedEvent`"
    ]


def test_the_decorator_records_what_the_handler_declared():
    """The registry is only as good as what the decorator captured."""
    from faststream.redis import RedisRouter

    router = RedisRouter()
    before = len(ss.registered_subscriber_event_bindings())

    @ss.reliable_redis_stream_subscriber(
        router,
        "contract_probe_events",
        group="contract-probe",
        consumer="contract-probe-consumer",
    )
    async def handler(event: dict) -> None:
        del event

    del handler
    recorded = ss.registered_subscriber_event_bindings()

    assert len(recorded) == before + 1
    assert recorded[-1].stream == "contract_probe_events"
    assert recorded[-1].group == "contract-probe"
    assert recorded[-1].annotation == "dict"
