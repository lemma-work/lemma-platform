"""Product-level counters: what customers do, as metrics rather than logs.

One place for the instruments so the label vocabulary is shared and bounded.
Every label value here comes from a fixed set in this file -- callers pass
whatever they have and the normalisers below fold anything unrecognised into
``other``, so a new platform or a typo cannot mint a series per value.

Recorded at the call sites that own each transition (conversation create,
inbound accept, outbound send, workflow/function terminal transitions,
connector execute, sandbox release). The gauges are sampled on the worker by
the work-gauge loop (``app.core.infrastructure.work.gauges``) and report
nothing until their first successful sample.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

meter = metrics.get_meter(__name__)

#: The surfaces a conversation or message can arrive on. ``workflow`` and
#: ``other`` exist so nothing is mislabelled as ``app``: a workflow step and a
#: notification thread are conversations a person did not open.
SURFACES: Final = frozenset(
    {
        "app",
        "slack",
        "telegram",
        "whatsapp",
        "teams",
        "email",
        "web_widget",
        "api",
        "workflow",
        "other",
    }
)
ACTIVE_USER_WINDOWS: Final = {"5m": 300, "1h": 3600, "24h": 86400}

# SurfacePlatform values (agent_surfaces) and the labels a conversation's
# ``origin`` is counted under (``conversation_metadata.surface_of``) both map
# onto the surface vocabulary here.
_SURFACE_ALIASES: Final = {"resend": "email", "agent_surfaces": "other"}


def surface_label(value: str | None) -> str:
    """Fold a platform name or conversation source onto ``SURFACES``."""
    if not value:
        return "other"
    lowered = value.lower()
    lowered = _SURFACE_ALIASES.get(lowered, lowered)
    return lowered if lowered in SURFACES else "other"


def outcome_label(succeeded: bool) -> str:
    return "success" if succeeded else "failure"


conversations_started = meter.create_counter(
    "lemma.conversations.started",
    description="Root conversations created, by the surface they started on.",
)
surface_messages = meter.create_counter(
    "lemma.surface.messages",
    description="Messages through an external surface, by direction and outcome.",
)
surface_delivery_seconds = meter.create_histogram(
    "lemma.surface.delivery_seconds",
    unit="s",
    description="One outbound send to a surface's platform, start to answer.",
)
workflow_runs = meter.create_counter(
    "lemma.workflow.runs",
    description="Workflow runs that reached a terminal state, by outcome.",
)
workflow_run_duration = meter.create_histogram(
    "lemma.workflow.run_duration_seconds",
    unit="s",
    description="Workflow run wall time, started to terminal.",
)
function_runs = meter.create_counter(
    "lemma.function.runs",
    description="Function runs that reached a terminal state, by kind and outcome.",
)
function_run_duration = meter.create_histogram(
    "lemma.function.run_duration_seconds",
    unit="s",
    description="Function run wall time, started to terminal.",
)
connector_operations = meter.create_counter(
    "lemma.connector.operations",
    description="Connector operations executed, by catalog connector and outcome.",
)
connector_operation_duration = meter.create_histogram(
    "lemma.connector.operation_duration_seconds",
    unit="s",
    description="One connector operation, credential presentation included.",
)
schedule_runs = meter.create_counter(
    "lemma.schedule.runs",
    description="Schedule fire decisions, by schedule type and outcome.",
)
schedule_run_duration = meter.create_histogram(
    "lemma.schedule.run.duration",
    unit="s",
    description="A schedule run, fired to its target's terminal outcome.",
)
schedule_fire_lag = meter.create_histogram(
    "lemma.schedule.fire_lag",
    unit="s",
    description="How late a timer schedule fired: fire time minus due time.",
)
sandbox_session_seconds = meter.create_histogram(
    "lemma.sandbox.session_seconds",
    unit="s",
    description="How long an E2B sandbox ran, ready (or resumed) to released.",
)


def record_conversation_started(surface: str | None) -> None:
    conversations_started.add(1, {"surface": surface_label(surface)})


def record_surface_message(
    surface: str | None, *, direction: str, succeeded: bool
) -> None:
    _surface_message(surface, direction=direction, outcome=outcome_label(succeeded))


def _surface_message(surface: str | None, *, direction: str, outcome: str) -> None:
    surface_messages.add(
        1,
        {
            "surface": surface_label(surface),
            "direction": "in" if direction == "in" else "out",
            "outcome": outcome,
        },
    )


def record_surface_delivery(
    surface: str | None, *, delivered: bool, transient: bool, seconds: float
) -> None:
    """One outbound send. A transient failure is ``retry``, not ``failure``:
    the row goes back to pending and its eventual result is counted then, so
    ``success + failure`` is messages and ``retry`` is extra attempts."""
    if delivered:
        outcome = "success"
    elif transient:
        outcome = "retry"
    else:
        outcome = "failure"
    _surface_message(surface, direction="out", outcome=outcome)
    surface_delivery_seconds.record(seconds, {"surface": surface_label(surface)})


def workflow_outcome(status: str) -> str:
    """COMPLETED / FAILED / CANCELLED, lowercased; anything else is ``other``."""
    lowered = status.lower()
    return lowered if lowered in {"completed", "failed", "cancelled"} else "other"


def record_workflow_run(status: str, seconds: float | None) -> None:
    workflow_runs.add(1, {"outcome": workflow_outcome(status)})
    if seconds is not None:
        workflow_run_duration.record(seconds)


def record_function_run(kind: str, status: str, seconds: float | None) -> None:
    kind_label = kind.lower() if kind.lower() in {"api", "job"} else "other"
    function_runs.add(1, {"kind": kind_label, "outcome": workflow_outcome(status)})
    if seconds is not None:
        function_run_duration.record(seconds, {"kind": kind_label})


def record_connector_operation(
    connector: str, *, succeeded: bool, seconds: float
) -> None:
    # ``connector`` is a slug from the global connectors catalog (the table
    # has no tenant column), so the label is bounded by the catalog's size.
    connector_operations.add(
        1, {"connector": connector, "outcome": outcome_label(succeeded)}
    )
    connector_operation_duration.record(seconds, {"connector": connector})


def record_sandbox_session(seconds: float) -> None:
    sandbox_session_seconds.record(max(seconds, 0.0))


#: ``ScheduleType`` values, lowered. Anything else is ``other``.
SCHEDULE_TYPES: Final = frozenset({"time", "webhook", "datastore"})
#: How a fire was decided (``lemma.schedule.runs``): it started its target,
#: its filter said no, it failed before reaching a target, the run cap or the
#: daily fire cap held it back, or it tripped the failure breaker.
SCHEDULE_RUN_OUTCOMES: Final = frozenset(
    {"triggered", "filtered", "failed", "skipped", "breaker_tripped"}
)
#: How a started run ended (``lemma.schedule.run.duration``).
_SCHEDULE_TERMINAL: Final = {
    "completed": "completed",
    "target_failed": "failed",
    "cancelled": "cancelled",
}


def schedule_type_label(value: object) -> str:
    raw = getattr(value, "value", value)
    lowered = str(raw or "").lower()
    return lowered if lowered in SCHEDULE_TYPES else "other"


def schedule_target_label(*, agent_id: object, workflow_id: object) -> str:
    if workflow_id is not None:
        return "workflow"
    return "agent" if agent_id is not None else "other"


def record_schedule_run(schedule_type: object, outcome: str) -> None:
    schedule_runs.add(
        1,
        {
            "schedule_type": schedule_type_label(schedule_type),
            "outcome": outcome if outcome in SCHEDULE_RUN_OUTCOMES else "other",
        },
    )


def record_schedule_run_duration(
    schedule_type: object, status: str, seconds: float
) -> None:
    schedule_run_duration.record(
        max(seconds, 0.0),
        {
            "schedule_type": schedule_type_label(schedule_type),
            "outcome": _SCHEDULE_TERMINAL.get(status.lower(), "other"),
        },
    )


def record_schedule_fire_lag(schedule_type: object, seconds: float) -> None:
    schedule_fire_lag.record(
        max(seconds, 0.0), {"schedule_type": schedule_type_label(schedule_type)}
    )


@dataclass
class GaugeSnapshot:
    """Last successful reading. Empty means "not measured", never zero."""

    sandboxes_active: int | None = None
    active_users: dict[str, int] = field(default_factory=dict)
    #: Active schedules by ``(schedule_type, target)``.
    schedules_active: dict[tuple[str, str], int] = field(default_factory=dict)


snapshot = GaugeSnapshot()


def _observe_sandboxes(options: CallbackOptions) -> Iterable[Observation]:
    del options
    if snapshot.sandboxes_active is None:
        return []
    return [Observation(snapshot.sandboxes_active)]


def _observe_active_users(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(count, {"window": window})
        for window, count in snapshot.active_users.items()
    ]


def _observe_schedules(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(count, {"schedule_type": schedule_type, "target": target})
        for (schedule_type, target), count in snapshot.schedules_active.items()
    ]


meter.create_observable_gauge(
    "lemma.schedules.active",
    callbacks=[_observe_schedules],
    description="Active, non-internal schedules, by type and target.",
)
meter.create_observable_gauge(
    "lemma.sandbox.sessions_active",
    callbacks=[_observe_sandboxes],
    description="E2B sandbox instances currently running (state ready).",
)
meter.create_observable_gauge(
    "lemma.active_users",
    callbacks=[_observe_active_users],
    description="Distinct users with model usage in the trailing window.",
)
