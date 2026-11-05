"""Product counters: the names, the labels that survive export, the values.

Read through ``metric_capture``, which applies production's metric View, so a
label the allowlist would strip is missing here too.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.core.infrastructure.work import gauges
from app.core.observability import product_metrics as pm
from app.modules.test_support.metric_capture import metric_reader, points, total


@pytest.fixture(autouse=True)
def _reader() -> None:
    metric_reader()


def _delta(name: str, action, **attributes: object) -> float:
    before = total(name, **attributes)
    action()
    return total(name, **attributes) - before


@pytest.mark.parametrize(
    "given, label",
    [
        ("SLACK", "slack"),
        ("RESEND", "email"),
        ("web_widget", "web_widget"),
        ("app", "app"),
        (None, "other"),
        ("agent_surfaces", "other"),
        ("a-platform-nobody-added", "other"),
    ],
)
def test_surface_labels_are_folded_onto_a_fixed_set(given, label) -> None:
    assert pm.surface_label(given) == label
    assert pm.surface_label(given) in pm.SURFACES


def test_conversation_started_is_counted_by_surface() -> None:
    assert (
        _delta(
            "lemma.conversations.started",
            lambda: pm.record_conversation_started("TELEGRAM"),
            surface="telegram",
        )
        == 1
    )


@pytest.mark.parametrize(
    "delivered, transient, outcome",
    [(True, False, "success"), (False, True, "retry"), (False, False, "failure")],
)
def test_an_outbound_send_counts_its_outcome_and_its_latency(
    delivered, transient, outcome
) -> None:
    labels = {"surface": "whatsapp", "direction": "out", "outcome": outcome}
    latency_before = total("lemma.surface.delivery_seconds", surface="whatsapp")
    assert (
        _delta(
            "lemma.surface.messages",
            lambda: pm.record_surface_delivery(
                "WHATSAPP", delivered=delivered, transient=transient, seconds=0.25
            ),
            **labels,
        )
        == 1
    )
    assert (
        total("lemma.surface.delivery_seconds", surface="whatsapp") - latency_before
        == 1
    )


@pytest.mark.parametrize("succeeded, outcome", [(True, "success"), (False, "failure")])
def test_an_inbound_message_counts_its_outcome(succeeded, outcome) -> None:
    assert (
        _delta(
            "lemma.surface.messages",
            lambda: pm.record_surface_message(
                "SLACK", direction="in", succeeded=succeeded
            ),
            surface="slack",
            direction="in",
            outcome=outcome,
        )
        == 1
    )


@pytest.mark.parametrize(
    "status, outcome",
    [("COMPLETED", "completed"), ("FAILED", "failed"), ("CANCELLED", "cancelled")],
)
def test_workflow_runs_count_each_terminal_outcome_and_time_it(status, outcome) -> None:
    duration_before = total("lemma.workflow.run_duration_seconds")
    assert (
        _delta(
            "lemma.workflow.runs",
            lambda: pm.record_workflow_run(status, 3.0),
            outcome=outcome,
        )
        == 1
    )
    assert total("lemma.workflow.run_duration_seconds") - duration_before == 1


def test_a_run_that_never_started_is_counted_but_not_timed() -> None:
    duration_before = total("lemma.workflow.run_duration_seconds")
    pm.record_workflow_run("CANCELLED", None)
    assert total("lemma.workflow.run_duration_seconds") == duration_before


@pytest.mark.parametrize("kind", ["API", "JOB"])
@pytest.mark.parametrize(
    "status, outcome", [("COMPLETED", "completed"), ("FAILED", "failed")]
)
def test_function_runs_carry_kind_and_outcome(kind, status, outcome) -> None:
    label = kind.lower()
    duration_before = total("lemma.function.run_duration_seconds", kind=label)
    assert (
        _delta(
            "lemma.function.runs",
            lambda: pm.record_function_run(kind, status, 1.5),
            kind=label,
            outcome=outcome,
        )
        == 1
    )
    assert (
        total("lemma.function.run_duration_seconds", kind=label) - duration_before == 1
    )


def test_the_gauges_report_nothing_until_sampled_then_the_reading() -> None:
    pm.snapshot.sandboxes_active = None
    pm.snapshot.active_users = {}
    assert points("lemma.sandbox.sessions_active") == []
    assert points("lemma.active_users") == []

    pm.snapshot.sandboxes_active = 4
    pm.snapshot.active_users = {"5m": 1, "1h": 2, "24h": 3}
    assert [p.value for p in points("lemma.sandbox.sessions_active")] == [4]
    assert {p.attributes["window"]: p.value for p in points("lemma.active_users")} == {
        "5m": 1,
        "1h": 2,
        "24h": 3,
    }


def test_sandbox_session_seconds_are_recorded() -> None:
    assert (
        _delta("lemma.sandbox.session_seconds", lambda: pm.record_sandbox_session(60))
        == 1
    )


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_a_failing_sampler_forgets_its_reading_and_spares_the_others() -> None:
    seen: list[str] = []
    forgotten: list[str] = []

    async def fails(session) -> None:
        raise OperationalError("SELECT", {}, Exception("down"))

    async def works(session) -> None:
        seen.append("works")

    registered = dict(gauges._samplers)
    gauges._samplers.clear()
    gauges.register_gauge_sampler(
        "test-fails", fails, forget=lambda: forgotten.append("fails")
    )
    gauges.register_gauge_sampler("test-works", works, forget=lambda: None)
    gauges.register_gauge_sampler("test-slow", works, forget=lambda: None, every=2)
    try:
        await gauges._run_samplers(_Session, tick=1)
    finally:
        gauges._samplers.clear()
        gauges._samplers.update(registered)

    assert forgotten == ["fails"]
    # The every-second-tick sampler skipped tick 1.
    assert seen == ["works"]


class _DownDatabase:
    async def __aenter__(self) -> None:
        raise OSError("database down")

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_a_failed_main_sample_forgets_every_registered_sampler() -> None:
    """Otherwise their last readings stand, looking healthy, through the outage."""
    import asyncio

    forgotten = asyncio.Event()

    async def never(session) -> None:
        raise AssertionError("samplers do not run when the main sample fails")

    registered = dict(gauges._samplers)
    gauges._samplers.clear()
    gauges.register_gauge_sampler("test-active", never, forget=forgotten.set)
    loop = asyncio.create_task(gauges.gauge_loop(_DownDatabase, 0))
    try:
        await asyncio.wait_for(forgotten.wait(), timeout=2)
    finally:
        loop.cancel()
        gauges._samplers.clear()
        gauges._samplers.update(registered)


@pytest.mark.parametrize(
    ("given", "label"),
    [
        ("TIME", "time"),
        ("WEBHOOK", "webhook"),
        ("DATASTORE", "datastore"),
        ("X", "other"),
    ],
)
def test_schedule_types_fold_onto_a_fixed_set(given, label) -> None:
    assert pm.schedule_type_label(given) == label


def test_a_late_timer_fire_records_its_lag() -> None:
    assert (
        _delta(
            "lemma.schedule.fire_lag",
            lambda: pm.record_schedule_fire_lag("TIME", 2.5),
            schedule_type="time",
        )
        == 1
    )


def test_an_unknown_run_outcome_is_other_not_a_new_series() -> None:
    assert (
        _delta(
            "lemma.schedule.runs",
            lambda: pm.record_schedule_run("TIME", "exploded"),
            schedule_type="time",
            outcome="other",
        )
        == 1
    )


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _ScheduleSession:
    async def execute(self, statement):
        from types import SimpleNamespace as Row

        return _Rows(
            [
                Row(schedule_type="TIME", has_workflow=False, has_agent=True, n=3),
                Row(schedule_type="WEBHOOK", has_workflow=True, has_agent=False, n=2),
                Row(schedule_type="NEW", has_workflow=False, has_agent=False, n=1),
            ]
        )


@pytest.mark.asyncio
async def test_active_schedules_are_grouped_by_type_and_target() -> None:
    from app.modules.schedule.infrastructure.schedules_gauge import (
        sample_active_schedules,
    )

    await sample_active_schedules(_ScheduleSession())  # type: ignore[arg-type]
    try:
        assert {
            (p.attributes["schedule_type"], p.attributes["target"]): p.value
            for p in points("lemma.schedules.active")
        } == {("time", "agent"): 3, ("webhook", "workflow"): 2, ("other", "other"): 1}
    finally:
        pm.snapshot.schedules_active = {}
