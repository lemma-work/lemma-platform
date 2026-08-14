"""A stalled loop has to say what stalled it.

The lag watchdog measures how late a wake-up fired, which it can only do once
the loop is running again — by then the blocking call has returned and there is
nothing to point at. Every stall in production reported a number and no name.

The sampler watches from an OS thread, so it runs *during* the stall and the
frame it grabs is the blocking call itself. These tests block a real loop with
a real `time.sleep` and assert the report names this file's function.
"""

from __future__ import annotations

import asyncio
import time
import traceback

import pytest

from app.core.observability.stall_sampler import (
    LoopStallSampler,
    format_stall_stack,
)

pytestmark = pytest.mark.unit


def _the_call_that_blocks_the_loop(seconds: float) -> None:
    """Deliberately named: the assertions look for this name in the stack."""
    time.sleep(seconds)


@pytest.mark.asyncio
async def test_the_report_names_the_function_that_blocked_the_loop() -> None:
    sampler = LoopStallSampler(
        stall_seconds=0.2, service_name="test", poll_seconds=0.02
    )
    captured: list[str] = []
    sampler._report = lambda stalled_for: captured.append(sampler._capture() or "")  # type: ignore[method-assign] # noqa: SLF001

    sampler.start()
    try:
        sampler.note_loop_alive()
        _the_call_that_blocks_the_loop(0.6)
        # Give the sampler thread a turn now that the loop is free again.
        await asyncio.sleep(0.1)
    finally:
        sampler.stop()

    assert captured, "the sampler never fired during a 600ms stall"
    assert "_the_call_that_blocks_the_loop" in captured[0]


@pytest.mark.asyncio
async def test_a_loop_that_keeps_getting_turns_is_never_reported() -> None:
    """The cost of this thing when nothing is wrong must be zero reports."""
    sampler = LoopStallSampler(
        stall_seconds=0.2, service_name="test", poll_seconds=0.02
    )

    sampler.start()
    try:
        for _ in range(20):
            sampler.note_loop_alive()
            await asyncio.sleep(0.02)
    finally:
        sampler.stop()

    assert sampler.reports == 0


@pytest.mark.asyncio
async def test_one_report_per_incident_not_one_per_poll() -> None:
    """At a 20ms poll a two-second stall is a hundred chances to log the same
    stack. The cooldown is what keeps an incident to one line."""
    sampler = LoopStallSampler(
        stall_seconds=0.1,
        service_name="test",
        poll_seconds=0.02,
        cooldown_seconds=60.0,
    )

    sampler.start()
    try:
        sampler.note_loop_alive()
        _the_call_that_blocks_the_loop(0.8)
        await asyncio.sleep(0.1)
    finally:
        sampler.stop()

    assert sampler.reports == 1


def test_the_stack_is_trimmed_to_application_frames() -> None:
    frames = traceback.extract_stack()
    noise = traceback.FrameSummary(
        "/usr/lib/python3.14/asyncio/base_events.py", 1, "run_forever"
    )
    mine = traceback.FrameSummary("/app/modules/agent/thing.py", 42, "do_the_work")

    rendered = format_stall_stack([*frames, noise, mine])

    assert "do_the_work" in rendered
    assert "run_forever" not in rendered


def test_a_stack_of_nothing_but_noise_still_reports_something() -> None:
    """Trimming must never turn a real stall into an empty log line."""
    noise = [
        traceback.FrameSummary(
            "/usr/lib/python3.14/asyncio/base_events.py", 1, "run_forever"
        )
    ]

    assert "run_forever" in format_stall_stack(noise)
