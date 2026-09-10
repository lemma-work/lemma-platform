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
import queue
import threading
import time
import traceback
from pathlib import Path

import pytest

from app.core.observability.stall_sampler import (
    _MAX_STACK_CHARS,
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
    sampler._report = lambda stalled_for: captured.append(sampler._capture()[0] or "")  # type: ignore[method-assign] # noqa: SLF001

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


def test_a_loop_parked_in_the_selector_says_so() -> None:
    """The production case, and the reason two audits could name no culprit.

    When the innermost frame is the selector the loop is not blocked in our
    code: it is waiting to be scheduled, or waiting to get the GIL back from
    another thread. Trimming that away left every report ending at
    ``run_until_complete`` with no application frame below it — identical text
    for every incident, and no answer in it.
    """
    parked = [
        traceback.FrameSummary("/app/lemma_cloud/worker.py", 42, "main"),
        traceback.FrameSummary(
            "/usr/lib/python3.14/asyncio/base_events.py", 1, "run_forever"
        ),
        traceback.FrameSummary("/usr/lib/python3.14/selectors.py", 7, "select"),
    ]

    rendered = format_stall_stack(parked)

    assert "select" in rendered, (
        "the innermost frame was filtered out, so the report cannot distinguish "
        "'parked in the selector' from 'blocked in our own code'"
    )


def _the_thread_that_holds_the_gil(
    stop: threading.Event, running: threading.Event
) -> None:
    """Burn CPU in pure Python, which is what actually holds the GIL."""
    running.set()
    while not stop.is_set():
        total = 0
        for index in range(5_000):
            total += index * index
        del total


@pytest.mark.asyncio
async def test_the_report_names_a_busy_thread_that_is_not_the_loop() -> None:
    """The blind spot: a stall the loop thread's own stack cannot explain.

    CPU-bound work on a ``run_blocking`` offload thread holds the GIL. The loop
    is runnable but cannot proceed, and its stack shows it parked in the
    selector — true, and useless alone. The sampler used to read only the loop
    thread, so the thread actually responsible was never looked at.
    """
    sampler = LoopStallSampler(
        stall_seconds=0.2, service_name="test", poll_seconds=0.02
    )
    sampler._loop_thread_id = threading.get_ident()  # noqa: SLF001

    stop = threading.Event()
    running = threading.Event()
    worker = threading.Thread(
        target=_the_thread_that_holds_the_gil,
        args=(stop, running),
        name="offload-worker",
        daemon=True,
    )
    worker.start()
    try:
        assert running.wait(5.0), "the worker thread never started"
        _, other_threads = sampler._capture()  # noqa: SLF001
    finally:
        stop.set()
        worker.join(timeout=5.0)

    assert other_threads, "no other thread was reported while one was burning CPU"
    assert "offload-worker" in other_threads
    # Asserted on the file, not on which function the sample happened to catch.
    # The claim is that a non-loop thread is reported with its *own* frames;
    # exactly where in the loop the GIL was released when the stack was grabbed
    # is scheduling, and pinning it made this flaky on a loaded CI runner.
    assert Path(__file__).name in other_threads


@pytest.mark.asyncio
async def test_threads_parked_waiting_for_work_are_not_reported() -> None:
    """An idle pool thread on every stall would bury the one that matters."""
    sampler = LoopStallSampler(
        stall_seconds=0.2, service_name="test", poll_seconds=0.02
    )
    sampler._loop_thread_id = threading.get_ident()  # noqa: SLF001

    idle = queue.Queue()
    parked = threading.Thread(
        target=lambda: idle.get(), name="parked-worker", daemon=True
    )
    parked.start()
    try:
        await asyncio.sleep(0.05)
        _, other_threads = sampler._capture()  # noqa: SLF001
    finally:
        idle.put(None)
        parked.join(timeout=2.0)

    assert "parked-worker" not in (other_threads or "")


def _frame(filename: str, name: str = "f", lineno: int = 1):
    return traceback.FrameSummary(filename, lineno, name, line="pass")


def test_a_deep_library_chain_still_names_our_call() -> None:
    """The `importlib` case: twelve frames of machinery, none of them ours.

    In production every import stall reported only `_find_and_load` /
    `_path_stat` frames, because the tail is all the trim kept. That says an
    import was slow and nothing about which one, which is the whole question.
    """
    frames = [
        _frame("/app/lemma-platform/lemma-backend/app/modules/x/gateway.py", "build"),
        *[_frame("<frozen importlib._bootstrap>", "_find_and_load") for _ in range(20)],
    ]

    rendered = format_stall_stack(frames)

    assert "gateway.py" in rendered
    assert "_find_and_load" in rendered


def test_trimming_keeps_both_ends() -> None:
    """A long stack loses its middle, not its head."""
    frames = [
        _frame("/app/lemma-platform/lemma-backend/app/entry.py", "outermost"),
        *[_frame(f"/very/long/library/path/mod_{i}.py", "x" * 200) for i in range(400)],
        _frame("/app/lemma-platform/lemma-backend/app/inner.py", "innermost"),
    ]

    rendered = format_stall_stack(frames)

    assert len(rendered) <= _MAX_STACK_CHARS
    assert "innermost" in rendered
