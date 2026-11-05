"""Cloud Logging's own trace fields, so a log line links to its trace.

``trace_id``/``span_id`` sit inside the JSON payload, where Logs Explorer and
Cloud Trace do not look, so an app log line could not be opened from its trace
or joined to the load balancer's line for the same request -- which carries
the native ``trace`` field. On GKE the logging agent lifts these three keys
out of the payload into those native fields.

The trace resource name needs the project the traces are exported to:
``OBSERVABILITY_TRACE_PROJECT_ID``, else ``GOOGLE_CLOUD_PROJECT``. With
neither set nothing is added, so a laptop or a non-GCP install is unchanged.
Read from the environment rather than settings because logging is configured
before, and imported by, the settings module.
"""

from __future__ import annotations

import os

from opentelemetry.trace import SpanContext

TRACE_KEY = "logging.googleapis.com/trace"
SPAN_KEY = "logging.googleapis.com/spanId"
SAMPLED_KEY = "logging.googleapis.com/trace_sampled"


def trace_project() -> str | None:
    for name in ("OBSERVABILITY_TRACE_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def cloud_trace_fields(span_context: SpanContext) -> dict[str, str | bool]:
    """The three keys for a valid span, or nothing without a project."""
    project = trace_project()
    if project is None:
        return {}
    return {
        TRACE_KEY: f"projects/{project}/traces/{span_context.trace_id:032x}",
        SPAN_KEY: f"{span_context.span_id:016x}",
        SAMPLED_KEY: span_context.trace_flags.sampled,
    }
