"""Worker event wiring and streaq task registration.

FastStream subscriptions and streaq tasks are registered from the module
registry: each module's ``event_routers()`` thunk imports its handlers, which
also registers any ``@streaq_task``/``@streaq_cron`` defined alongside them.

This runs at *import scope* on purpose: the worker entrypoint is
``streaq run app.events:streaq_worker``, so all tasks must be registered on the
shared ``streaq_worker`` before streaq introspects it, and all routers included
on the ``broker`` before ``broker.start()``.
"""

from app.core.infrastructure.jobs.streaq_runtime import broker, streaq_worker
from app.core.registry.assembly import wire_module_events
from app.core.registry.installed import OSS_MODULES

# Core-owned cron tasks register alongside module tasks.
from app.core.infrastructure.events import tasks as event_tasks  # noqa: F401

wire_module_events(OSS_MODULES, broker)

__all__ = ["broker", "streaq_worker"]
