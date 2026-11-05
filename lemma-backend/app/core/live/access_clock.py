"""When a live subscription's access is decided again.

Two triggers: the pod's grants epoch moved (``authz_epoch.pod_grants_epoch``),
or the last decision is ``LIVE_AUTHZ_RECHECK_SECONDS`` old whether it moved or
not -- a bump lost to Redis delays a revocation by at most that, never cancels
it. The epoch is taken as seen only once the re-check has gone through: one
that fails (the database away) is made again at the next check rather than
waiting for the next permission change.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from app.core.live.config import LiveSettings

EpochNow = Callable[[], Awaitable[str | None]]
Recheck = Callable[[], Awaitable[None]]


class AccessClock:
    def __init__(
        self,
        settings: LiveSettings,
        *,
        check_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._check_seconds = check_seconds
        self._clock = clock
        #: The epoch the last successful re-check was made under.
        self.epoch: str | None = None
        self.next_check = 0.0
        self._next_recheck = 0.0

    def _every(self) -> float:
        return self._check_seconds or self._settings.live_authz_check_seconds

    def start(self, epoch: str | None) -> None:
        """Access was just decided, under ``epoch``."""
        now = self._clock()
        self.epoch = epoch
        self.next_check = now + self._every()
        self._next_recheck = now + self._settings.live_authz_recheck_seconds

    async def check(self, epoch_now: EpochNow, recheck: Recheck) -> bool:
        """Run ``recheck`` if it is due; whether it ran. Its failure leaves
        the clock as it was, so the next check runs it again."""
        now = self._clock()
        self.next_check = now + self._every()
        epoch = await epoch_now()
        moved = epoch is not None and epoch != self.epoch
        if not moved and now < self._next_recheck:
            return False
        await recheck()
        if epoch is not None:
            self.epoch = epoch
        self._next_recheck = self._clock() + self._settings.live_authz_recheck_seconds
        return True


__all__ = ["AccessClock", "EpochNow", "Recheck"]
