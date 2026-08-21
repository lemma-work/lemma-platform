"""Build the process-wide analytics sink from settings, once, at startup.

The only place that decides whether this deployment reports anything. Absent
``ANALYTICS_WRITE_KEY`` the sink stays ``NullSink`` — which is what every
self-hosted server and every Desktop-local installation gets, because neither
sets that key.
"""

from __future__ import annotations

from app.core.analytics.emitter import configure, current_sink
from app.core.analytics.posthog import PostHogSink
from app.core.config import reveal_secret, settings


def start_analytics() -> None:
    key = (reveal_secret(settings.analytics_write_key) or "").strip()
    if not key:
        # Explicitly reinstall the null sink rather than leaving whatever a
        # previous process or test put there.
        configure(
            None, deployment=settings.environment, strict=settings.analytics_strict
        )
        return
    existing = current_sink()
    if isinstance(existing, PostHogSink) and existing.write_key == key:
        # `app/standalone.py` runs the API lifespan and the primary worker in
        # one process, so both call this. Replacing the sink would leave the
        # first flusher running with no way to close it and events buffered
        # behind a handle nobody holds.
        return
    sink = PostHogSink(write_key=key, host=settings.analytics_host)
    sink.start()
    configure(sink, deployment=settings.environment, strict=settings.analytics_strict)


async def stop_analytics() -> None:
    sink = current_sink()
    await sink.aclose()
    configure(None)
