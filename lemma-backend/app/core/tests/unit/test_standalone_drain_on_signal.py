"""The single-process app starts handing turns back on SIGTERM, not after
uvicorn has waited out every open event stream."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from app.standalone import _drain_on_signal


@pytest.mark.asyncio
async def test_sigterm_starts_the_drain_and_still_reaches_the_prior_handler() -> None:
    seen: list[str] = []

    def prior(signum, frame) -> None:
        seen.append("uvicorn")

    before = signal.signal(signal.SIGTERM, prior)
    try:
        restore = _drain_on_signal(lambda: seen.append("drain"))
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.05)
        assert seen == ["uvicorn", "drain"]
        restore()
        assert signal.getsignal(signal.SIGTERM) is prior
    finally:
        signal.signal(signal.SIGTERM, before)
