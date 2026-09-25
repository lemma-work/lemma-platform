"""Size limits for the general tracing pipeline.

The sanitizing exporter trims span content, but only at export: until then a
recorded span sits whole in the batch queue. These limits truncate at record
time and bound the queue, so a collector outage costs bounded memory.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import SpanLimits
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

# Phoenix renders these in full, so the cap is about what a span is allowed to
# weigh on the wire rather than about what is readable. A run's transcript can
# be megabytes; the OTLP batch it would ride in is not the place to find that
# out.
MAX_SPAN_CONTENT_CHARS = 8_192

# Per-attribute cap on the general pipeline, matching what the sanitizing
# exporter keeps of span content anyway. Truncating at record time matters: a
# bulk write's ~1 MB ``db.statement`` otherwise sat whole in every queued span
# -- up to the queue size of them per process.
GENERAL_SPAN_LIMITS = SpanLimits(
    max_span_attribute_length=MAX_SPAN_CONTENT_CHARS,
    max_attribute_length=MAX_SPAN_CONTENT_CHARS,
)

# Half the SDK default: with attributes capped each span is small, and a
# collector outage should cost bounded memory rather than 2048 spans' worth.
GENERAL_SPAN_QUEUE_SIZE = 1024


def general_span_processor(exporter: SpanExporter) -> BatchSpanProcessor:
    """The general pipeline's batch processor, with its bounded queue."""
    return BatchSpanProcessor(exporter, max_queue_size=GENERAL_SPAN_QUEUE_SIZE)
