"""Build the process-wide analytics sink from settings, once, at startup.

The only place that decides whether this deployment reports anything. Absent
``ANALYTICS_WRITE_KEY`` the sink stays ``NullSink`` — which is what every
self-hosted server and every Desktop-local installation gets, because neither
sets that key.
"""

from __future__ import annotations

from app.core.analytics.emitter import configure, current_sink
from app.core.analytics.posthog import PostHogSink
from app.core.config import settings


def start_analytics() -> None:
    key = (settings.analytics_write_key or "").strip()
    if not key:
        # Explicitly reinstall the null sink rather than leaving whatever a
        # previous process or test put there.
        configure(None, deployment=settings.environment, strict=settings.analytics_strict)
        return
    sink = PostHogSink(write_key=key, host=settings.analytics_host)
    sink.start()
    configure(sink, deployment=settings.environment, strict=settings.analytics_strict)


async def stop_analytics() -> None:
    sink = current_sink()
    await sink.aclose()
    configure(None)
