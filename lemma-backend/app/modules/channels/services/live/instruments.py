"""What a channels hub measures of itself, and how long it waits after a fault."""

from __future__ import annotations

from opentelemetry import metrics

meter = metrics.get_meter("app.modules.channels.services.live.hub")
hub_reads = meter.create_counter("lemma.live.channels.reads")
hub_resyncs = meter.create_counter("lemma.live.resyncs")
hub_restarts = meter.create_counter(
    "lemma.live.channels.hub_loop_failures",
    description="Channels hub loops that met an unforeseen error and carried on.",
)
hub_drain_seconds = meter.create_histogram(
    "lemma.live.channels.drain_seconds",
    unit="s",
    description="One channels hub read: what moved, projected to every reader.",
)

#: How long a hub that failed waits before reading again: doubling from the
#: first, to at most the last.
BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MOST_SECONDS = 30.0


def backoff(failures: int) -> float:
    """The wait after the ``failures``-th fault in a row."""
    return min(BACKOFF_MOST_SECONDS, BACKOFF_FIRST_SECONDS * 2 ** max(0, failures - 1))


__all__ = [
    "backoff",
    "hub_drain_seconds",
    "hub_reads",
    "hub_restarts",
    "hub_resyncs",
]
