"""``python -m app.worker`` with the agent turn's lease shortened for a test.

A SIGKILLed worker's turn is reclaimed once its lease runs out: 60 seconds in
production. ``E2E_TURN_LEASE_SECONDS`` scales that down so a crash-resume test
takes seconds; the heartbeat (lease / 3) scales with it, so nothing else about
the turn changes. Everything else is the production worker.
"""

import dataclasses
import os
from datetime import timedelta

from app.worker import main

from app.worker import register_worker_modules  # noqa: E402

register_worker_modules()  # every module's kinds, as the worker registers them
from app.core.infrastructure.work import registry  # noqa: E402
from app.modules.agent.events.work_kinds import AGENT_TURN_KINDS  # noqa: E402

_LEASE = float(os.environ.get("E2E_TURN_LEASE_SECONDS", "0"))
if _LEASE:
    for _kind in AGENT_TURN_KINDS:
        _spec = registry.get(_kind)
        registry._handlers[_kind] = dataclasses.replace(  # noqa: SLF001
            _spec, lease=timedelta(seconds=_LEASE)
        )


if __name__ == "__main__":
    main()
