"""Every event subscriber's dependency model must actually build.

FastStream turns each handler's signature into a pydantic model, and it does so
when a worker *starts* -- not when the module is imported and not when a test
calls the handler directly. So an annotation pydantic cannot build a validator
for is invisible to the whole unit lane, and the first thing that notices is a
worker exiting with `worker.startup.failed` on boot.

That is what happened: a handler took a dependency annotated with a `Protocol`
that was not `runtime_checkable`, pydantic could not build an `is-instance`
validator for it, and the worker refused to start. Every unit test on that
handler passed, because calling a function does not build its dependency model.

This builds them all, for every module the registry installs. It needs no
broker connection, no Redis and no database -- the failure is in the schema, and
the schema is built from the signature.
"""

from __future__ import annotations

import pytest
from faststream.redis import RedisBroker

from app.core.registry.assembly import wire_module_events
from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit


def test_every_event_subscribers_dependency_model_builds():
    broker = RedisBroker()
    wire_module_events(OSS_MODULES, broker)

    subscribers = list(getattr(broker, "subscribers", []))
    assert subscribers, "no subscribers were registered — this test proves nothing"

    for subscriber in subscribers:
        # The call the worker makes on startup, and the one that raises when a
        # handler's annotation cannot be turned into a validator.
        subscriber._build_fastdepends_model()
