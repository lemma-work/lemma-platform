"""The exact contract every application-owned log record is held to.

An app record must name an event that is in the generated catalog, at the
severity the catalog records, carrying only fields the catalog knows about.
Anything else is a contract violation, and this module owns what happens then.

Separate from `log.py` because it is the vocabulary rather than the pipeline:
the pattern an event name has to match, the metadata every record may carry,
when strict mode turns a violation into an exception, and the bookkeeping that
decides whether one more violating record is worth emitting.
"""

from __future__ import annotations

import os
import re

#: A stable event name: lowercase dotted identifiers, at least two segments.
STABLE_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

CONTRACT_METADATA_FIELDS = {
    "causation_id",
    "consumer",
    "correlation_id",
    "deployment.environment",
    "dropped_field_count",
    "dropped_fields",
    "error_frames",
    "error_message",
    "error_stack_hash",
    "error_traceback",
    "error_type",
    "event",
    "event_id",
    "event_type",
    "job_attempt",
    "job_id",
    "level",
    "logger",
    "release.sha",
    "request_id",
    "service.name",
    "service.version",
    "span_id",
    "task_name",
    "timestamp",
    "trace_id",
}


class LoggingContractError(ValueError):
    """Raised when local code violates the exact structured-log contract."""


def strict_logging_contract_enabled() -> bool:
    configured = os.getenv("LEMMA_LOGGING_CONTRACT_STRICT")
    if configured is None:
        configured = os.getenv("LOGGING_CONTRACT_STRICT")
    enabled = (configured or "").strip().lower() in {"1", "true", "yes", "on"}
    raw_environment = (
        (os.getenv("LEMMA_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "local")
        .strip()
        .lower()
    )
    return enabled and raw_environment in {"local", "test", "testing"}


#: One violation record per (offending event, kind of violation).
#:
#: This used to be one process-wide boolean: after the first violation, every
#: later violating record -- any event, for the life of the process -- was
#: dropped whole, error records included. An event missing from the catalog then
#: did not lose its fields, it disappeared, and the single record that was
#: emitted did not even name the offender. That is a class of failure the
#: operator cannot see, which is what the contract exists to prevent.
#:
#: Bounded because a violating event name can be built at runtime -- that is
#: what `invalid_event_name` means -- and an unbounded set of them is a slow
#: leak in a long-lived process. Past the bound, dropping resumes.
MAX_REPORTED_VIOLATIONS = 200
reported_violations: set[tuple[str, str]] = set()


def violation_offender(event: object, violation: str) -> str | None:
    """The name to report this violation under, or None to drop the record.

    The event is named only where the name itself passed the pattern check,
    which makes it a code-authored dotted identifier and therefore payload-free.
    An `invalid_event_name` is by definition something else -- possibly an
    interpolated string carrying user text -- so every one of those is counted
    under a single placeholder and none is ever rendered.
    """
    offender = "<invalid>" if violation == "invalid_event_name" else str(event)
    signature = (offender, violation)
    if (
        signature in reported_violations
        or len(reported_violations) >= MAX_REPORTED_VIOLATIONS
    ):
        return None
    reported_violations.add(signature)
    return offender
