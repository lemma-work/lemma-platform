"""Where product-analytics events go, behind one interface.

The vendor is deliberately invisible to call sites. Everything emits against
the catalog in :mod:`app.core.analytics.event_catalog`; a sink maps that to
PostHog today and self-hosted ClickStack later. At the swap, one class is added
and no call site moves.

``NullSink`` is the default, and it is a null object rather than a disabled
PostHog client on purpose: a self-hosted or Desktop-local deployment has no
code path that can be induced into sending pod content by flipping one boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


PropertyValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    """One analytics event, already sanitized. A sink forwards it verbatim.

    Anything reaching a sink has passed the catalog's default-deny allowlist,
    so a sink never filters, truncates, or re-checks. Sinks transport.
    """

    name: str
    distinct_id: str
    properties: Mapping[str, PropertyValue] = field(default_factory=dict)
    groups: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class AnalyticsSink(Protocol):
    def capture(self, event: CapturedEvent) -> None:
        """Accept one event. Must not block and must not raise.

        Analytics is never worth failing a request over, so a sink that cannot
        deliver drops instead of propagating. Transport happens elsewhere.
        """
        ...

    async def aclose(self) -> None:
        """Deliver what is buffered and release resources."""
        ...


class NullSink:
    """Accepts and discards. The default for every deployment that is not
    Lemma Cloud."""

    def capture(self, event: CapturedEvent) -> None:  # noqa: D102
        return None

    async def aclose(self) -> None:  # noqa: D102
        return None


class MemorySink:
    """Records events in order. For tests, including the safety canary."""

    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []

    def capture(self, event: CapturedEvent) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        return None
