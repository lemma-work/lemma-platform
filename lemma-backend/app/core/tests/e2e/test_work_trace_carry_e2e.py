"""Async work joins the trace of the request that queued it.

Through the real writer and a real ``work_items`` row: the item is queued
inside a span, read back the way a claim reads it, and executed by the
dispatcher. Its ``work.execute`` span must be a child of the enqueuing span,
and must follow that span's sampling decision -- including a forced one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from sqlalchemy import text

from app.core.infrastructure.work import dispatcher as dispatcher_module
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.lifecycle import item_from_row
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.types import ClaimedItem, WorkClass, WorkMode
from app.core.observability import forced_sampling
from app.core.tests.e2e.work_support import POD_A, Demo, WorkDb

pytestmark = [pytest.mark.e2e]

_NEVER = ParentBased(TraceIdRatioBased(0.0))


def _provider(sampler: Sampler) -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=forced_sampling.ForcibleSampler(sampler))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


@pytest.fixture
def demo_kind(registry: WorkRegistry) -> WorkRegistry:
    @registry.handler(
        kind="demo.traced", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        return None

    return registry


async def _claimed(work_db: WorkDb, item_id) -> ClaimedItem:
    async with work_db.sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, ns, class, kind, payload, dedup_key, attempts, "
                    "lease_losses, first_unavailable_at, run_at, created_at, "
                    "lease_epoch FROM work_items WHERE id = :id"
                ),
                {"id": item_id},
            )
        ).one()
    return item_from_row(row)


async def _execute(
    item: ClaimedItem, worker: TracerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run_fast(env, spec, claimed):
        return "done"

    monkeypatch.setattr(dispatcher_module, "tracer", worker.get_tracer("worker"))
    monkeypatch.setattr(dispatcher_module, "run_fast", run_fast)
    env = SimpleNamespace(
        registry=SimpleNamespace(get=lambda kind: SimpleNamespace(mode=WorkMode.FAST)),
        local_wake=None,
    )
    dispatcher = dispatcher_module.Dispatcher(
        WorkClass.EVENTS,
        env,  # type: ignore[arg-type]
        slots=1,
        quantum=1,
        poll_interval=1.0,
    )
    assert await dispatcher.execute(item) == "done"


def _work_spans(exporter: InMemorySpanExporter) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == "work.execute"]


async def test_work_queued_in_a_sampled_span_runs_as_its_child(
    work_db: WorkDb, demo_kind: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = _provider(ALWAYS_ON)
    with api.get_tracer("api").start_as_current_span("GET /pods") as request:
        item_id = await work_db.enqueue(demo_kind, "demo.traced", POD_A, Demo())
    item = await _claimed(work_db, item_id)
    assert item.payload == {"tag": ""}  # the carried context is not the handler's

    worker, exporter = _provider(_NEVER)
    await _execute(item, worker, monkeypatch)

    (span,) = _work_spans(exporter)
    assert span.context.trace_id == request.get_span_context().trace_id
    assert span.parent is not None
    assert span.parent.span_id == request.get_span_context().span_id


async def test_work_queued_by_an_always_traced_account_is_sampled_in_the_worker(
    work_db: WorkDb, demo_kind: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = _provider(_NEVER)
    token = forced_sampling._forced.set(True)
    try:
        with api.get_tracer("api").start_as_current_span("GET /pods") as request:
            item_id = await work_db.enqueue(demo_kind, "demo.traced", POD_A, Demo())
    finally:
        forced_sampling._forced.reset(token)
    assert request.get_span_context().trace_flags.sampled

    worker, exporter = _provider(_NEVER)  # the worker's own ratio keeps nothing
    await _execute(await _claimed(work_db, item_id), worker, monkeypatch)

    (span,) = _work_spans(exporter)
    assert span.context.trace_id == request.get_span_context().trace_id


async def test_work_queued_outside_any_span_is_a_root(
    work_db: WorkDb, demo_kind: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = await work_db.enqueue(demo_kind, "demo.traced", POD_A, Demo())
    worker, exporter = _provider(ALWAYS_ON)
    await _execute(await _claimed(work_db, item_id), worker, monkeypatch)

    (span,) = _work_spans(exporter)
    assert span.parent is None
    assert span.links == ()


async def test_a_rearmed_periodic_links_to_its_last_run_instead_of_chaining(
    work_db: WorkDb, demo_kind: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = _provider(ALWAYS_ON)
    with api.get_tracer("w").start_as_current_span("work.execute") as last_run:
        item_id = await work_db.enqueue(
            demo_kind, "demo.traced", POD_A, Demo(), trace="link"
        )
    worker, exporter = _provider(ALWAYS_ON)
    await _execute(await _claimed(work_db, item_id), worker, monkeypatch)

    (span,) = _work_spans(exporter)
    assert span.parent is None
    assert span.context.trace_id != last_run.get_span_context().trace_id
    (link,) = span.links
    assert link.context.span_id == last_run.get_span_context().span_id
