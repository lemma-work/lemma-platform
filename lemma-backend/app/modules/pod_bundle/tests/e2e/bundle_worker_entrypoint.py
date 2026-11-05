"""``python -m app.worker`` with the bundle jobs' lease shortened, for a test.

A SIGKILLed worker's job is reclaimed once its lease runs out: 60 seconds in
production. ``E2E_BUNDLE_LEASE_SECONDS`` scales that down so a crash-resume
test takes seconds (the heartbeat, lease / 3, scales with it), and
``E2E_BUNDLE_STEP_DELAY_SECONDS`` makes each apply step take that long, so a
kill lands between two committed steps. Everything else is the production
worker.
"""

import asyncio
import dataclasses
import os
from datetime import timedelta

from app.worker import main

from app.worker import register_worker_modules  # noqa: E402

register_worker_modules()  # every module's kinds, as the worker registers them
from app.core.infrastructure.work import registry  # noqa: E402
from app.modules.pod_bundle.events import work as bundle_work  # noqa: E402
from app.modules.pod_bundle.infrastructure import applier  # noqa: E402

_LEASE = float(os.environ.get("E2E_BUNDLE_LEASE_SECONDS", "0"))
if _LEASE:
    for _kind in (
        bundle_work.EXPORT,
        bundle_work.PLAN,
        bundle_work.IMPORT_GITHUB,
        bundle_work.IMPORT_URL,
        bundle_work.APPLY,
        bundle_work.PUBLISH,
    ):
        _spec = registry.get(_kind)
        registry._handlers[_kind] = dataclasses.replace(  # noqa: SLF001
            _spec, lease=timedelta(seconds=_LEASE)
        )

_DELAY = float(os.environ.get("E2E_BUNDLE_STEP_DELAY_SECONDS", "0"))
if _DELAY:
    _apply_step = applier.BundleApplier.apply_step

    async def _slow_apply_step(self, step):  # noqa: ANN001
        await asyncio.sleep(_DELAY)
        await _apply_step(self, step)

    applier.BundleApplier.apply_step = _slow_apply_step  # type: ignore[method-assign]


if __name__ == "__main__":
    main()
