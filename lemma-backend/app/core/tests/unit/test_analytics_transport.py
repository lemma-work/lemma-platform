"""The sink must be inert when unconfigured and bounded when it cannot deliver.

Two properties, and both are load-bearing for a product that ships self-hosted
and on a laptop:

* **Unconfigured is nothing.** No key means a null object, no background task,
  no outbound socket -- not a disabled client that one boolean could wake.
* **Configured but unreachable is still bounded.** A PostHog outage must cost
  dropped events and a couple of seconds at shutdown. Never a retry storm, never
  a hung pod, never a flusher that dies silently and takes the rest of the
  process's analytics with it.

Modelled on ``test_backlog_gauges.py``, which holds the same shape for the
metrics sampler.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.analytics import posthog as posthog_module
from app.core.analytics.bootstrap import start_analytics, stop_analytics
from app.core.analytics.emitter import configure, current_sink
from app.core.analytics.posthog import PostHogSink
from app.core.analytics.sink import CapturedEvent, NullSink
from app.core.config import settings


def _event(name: str = "pod.created") -> CapturedEvent:
    return CapturedEvent(name=name, distinct_id="user-1", properties={"a": 1})


class _RecordingClient:
    """Stands in for the shared httpx client and remembers how it was called."""

    def __init__(self, status: int = 200, delay: float = 0.0) -> None:
        self.status = status
        self.delay = delay
        self.calls: list[dict] = []

    async def post(self, url: str, *, json: dict, timeout) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.delay:
            await asyncio.sleep(self.delay)
        return httpx.Response(self.status, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def _restore_sink():
    yield
    configure(None)


# -- unconfigured is nothing ---------------------------------------------


def test_no_key_installs_the_null_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "analytics_write_key", None)
    start_analytics()
    assert isinstance(current_sink(), NullSink)


def test_a_blank_key_is_the_same_as_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the ``.strip()``: a key set to whitespace by a templating mistake
    must not be treated as a key."""
    monkeypatch.setattr(settings, "analytics_write_key", "   ")
    start_analytics()
    assert isinstance(current_sink(), NullSink)


async def test_an_unconfigured_process_starts_no_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "analytics_write_key", None)
    before = {t.get_name() for t in asyncio.all_tasks()}
    start_analytics()
    after = {t.get_name() for t in asyncio.all_tasks()}
    assert "analytics-flush" not in (after - before)


async def test_starting_twice_keeps_one_sink_and_one_flusher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`standalone.py` runs the API lifespan and the primary worker in one
    process, so both call `start_analytics`. The second must not orphan the
    first sink's flusher."""
    monkeypatch.setattr(settings, "analytics_write_key", "phc_test")
    start_analytics()
    first = current_sink()
    start_analytics()
    assert current_sink() is first
    assert len([t for t in asyncio.all_tasks() if t.get_name() == "analytics-flush"]) == 1
    await stop_analytics()


# -- configured but unreachable is bounded --------------------------------


async def test_delivery_never_out_waits_the_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare float would set every httpx phase, including ``pool``. Request-path
    callers get a 5s pool budget; analytics must yield rather than outrank them
    for the last free connection."""
    client = _RecordingClient()
    # Patch the name the sink actually resolves -- `posthog.py` imported it.
    monkeypatch.setattr(posthog_module, "get_shared_http_client", lambda: client)
    sink = PostHogSink(write_key="phc_test", host="https://example.invalid")
    sink.capture(_event())
    await sink._drain_once()

    timeout = client.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout), "a bare float would override pool too"
    assert timeout.pool is not None and timeout.pool < 5.0


async def test_delivery_failures_are_dropped_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole optionality story rests on this: a sink that cannot deliver
    drops. Anyone adding a retry queue has to delete this test first."""
    client = _RecordingClient(status=500)
    # Patch the name the sink actually resolves -- `posthog.py` imported it.
    monkeypatch.setattr(posthog_module, "get_shared_http_client", lambda: client)
    sink = PostHogSink(write_key="phc_test", host="https://example.invalid")
    sink.capture(_event())
    await sink._drain_once()

    assert len(client.calls) == 1
    assert not sink._buffer


async def test_shutdown_is_bounded_when_the_endpoint_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full buffer against a dead endpoint is dozens of sequential posts. The
    drain must give up, not hold the pod's SIGTERM open."""
    client = _RecordingClient(delay=10.0)
    # Patch the name the sink actually resolves -- `posthog.py` imported it.
    monkeypatch.setattr(posthog_module, "get_shared_http_client", lambda: client)
    sink = PostHogSink(write_key="phc_test", host="https://example.invalid")
    for _ in range(sink._buffer.maxlen or 10_000):
        sink.capture(_event())

    await asyncio.wait_for(sink.aclose(), timeout=5.0)


async def test_a_failing_drain_does_not_kill_the_flusher() -> None:
    """One escaped exception used to end the flusher for the life of the
    process, after which every event was silently dropped."""
    sink = PostHogSink(
        write_key="phc_test", host="https://example.invalid", flush_interval_seconds=0.01
    )
    calls = {"n": 0}

    async def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    sink._drain_once = flaky  # type: ignore[method-assign]
    sink.start()
    try:
        await asyncio.sleep(0.1)
        assert calls["n"] > 1, "the flusher stopped after the first failure"
        assert sink._task is not None and not sink._task.done()
    finally:
        sink._stopping.set()
        if sink._task is not None:
            sink._task.cancel()


async def test_cancellation_is_never_swallowed_by_the_error_handling() -> None:
    """The broad catch in the flush loop must not eat a cancellation aimed at
    the task -- that is how a worker refuses to shut down."""
    sink = PostHogSink(
        write_key="phc_test", host="https://example.invalid", flush_interval_seconds=0.01
    )

    async def hang() -> None:
        await asyncio.sleep(3600)

    sink._drain_once = hang  # type: ignore[method-assign]
    sink.start()
    await asyncio.sleep(0.05)
    task = sink._task
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


async def test_close_is_idempotent() -> None:
    sink = PostHogSink(write_key="phc_test", host="https://example.invalid")
    await sink.aclose()
    await sink.aclose()


# -- analytics must never fail a request ----------------------------------


async def test_recording_an_app_session_cannot_fail_authentication() -> None:
    """`verify_auth` wraps its body in a broad ``except Exception`` that becomes
    a 401, so anything escaping the analytics call would refuse a valid session.

    This shipped once: a connection object without ``.headers`` raised straight
    through and turned six authentication paths into 401s. Analytics is never
    worth failing a request over, and certainly not authentication.
    """
    from types import SimpleNamespace

    from app.composition.app_session import maybe_record_app_session

    class _Exploding:
        @property
        def headers(self):
            raise RuntimeError("boom")

    # No headers at all, headers that raise, and a session that is not one.
    await maybe_record_app_session(SimpleNamespace(), object(), "user-1")
    await maybe_record_app_session(_Exploding(), object(), "user-1")
    await maybe_record_app_session(
        SimpleNamespace(headers={"X-Lemma-App": "not-a-uuid"}), object(), "user-1"
    )
