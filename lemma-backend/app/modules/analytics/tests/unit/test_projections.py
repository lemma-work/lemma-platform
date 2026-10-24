"""What the projections send: the scope, the outcomes, and the declared properties.

**Scope.** Every pod-scoped event reaches the warehouse with both of its groups.
The catalog declares ``organization`` and ``pod`` for every pod-scoped event, and
for most of them only ``pod`` was sent: the source events name a pod and not the
organization holding it. Organization-level retention -- the account, the unit
the catalog calls the one that pays -- was computed over a handful of events and
silently missed the rest.

So this drives one committed domain event of every projected type through its
projection, its reads answered through the ``ProjectionReads`` seam, and holds
the rule over all of them at once: an event whose catalog entry declares the
organization group carries it. The parity e2e holds the same rule over real
reads, ``pod.delivered`` and ``app.session_started`` included.

**Outcomes.** Only a run that completed is an outcome: a failed one is measured
and neither activates its pod nor counts as a surface answered.

**Properties.** A property the catalog declares and nothing fills in is a
dashboard column that is always empty, so each one is asserted filled.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.analytics import ANALYTICS_CATALOG, MemorySink, configure
from app.core.domain.events import DomainEvent
from app.modules.agent.domain.events import (
    AgentCreatedEvent,
    AgentRunCompletedEvent,
    ConversationStartedEvent,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent_surfaces.domain.events import SurfaceConnectedEvent
from app.modules.analytics.events import WIRED_EVENTS
from app.modules.analytics.events import agent as agent_events
from app.modules.analytics.events import apps as app_events
from app.modules.analytics.events import connectors as connector_events
from app.modules.analytics.events import datastore as datastore_events
from app.modules.analytics.events import function as function_events
from app.modules.analytics.events import identity as identity_events
from app.modules.analytics.events import pod as pod_events
from app.modules.analytics.events import pod_bundle as bundle_events
from app.modules.analytics.events import schedule as schedule_events
from app.modules.analytics.events import surfaces as surface_events
from app.modules.analytics.events import workflow as workflow_events
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    forget_pod_organizations,
)
from app.modules.analytics.services.pod_delivery import DeliveryVia
from app.modules.apps.domain.events import AppCreatedEvent, AppPublishedEvent
from app.modules.connectors.domain.analytics import operation_execution_recorded
from app.modules.connectors.domain.events import ConnectorConnectedEvent
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.datastore.domain.events import (
    DatastoreFileCreatedEvent,
    DatastoreTableCreatedEvent,
)
from app.modules.function.domain.events import FunctionCreatedEvent
from app.modules.identity.domain.events import (
    OrganizationCreatedEvent,
    OrganizationMemberAddedEvent,
    UserSignedUpEvent,
)
from app.modules.pod.domain.events import (
    PodCreatedEvent,
    PodDeletedEvent,
    PodMemberAddedEvent,
)
from app.modules.pod_bundle.domain.events import (
    BundleExportedEvent,
    BundleImportCompletedEvent,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleCreated,
    ScheduleRunCompleted,
)
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.workflow.domain.events import (
    WorkflowCreatedEvent,
    WorkflowRunTerminalEvent,
)
from app.modules.workflow.domain.run import WorkflowRunStatus

ORG = uuid4()
POD = uuid4()
USER = uuid4()
BUILDER = uuid4()
SURFACE = uuid4()
_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: Raised outside the projections: the connector's by a test below, the other
#: two by the parity e2e, which has the database and Redis they read.
_RAISED_ELSEWHERE = frozenset(
    {"pod.delivered", "app.session_started", "connector.operation_executed"}
)


class _Uow:
    async def commit(self) -> None:
        return None


class _UowFactory:
    """A unit of work nothing reads through: every read below is stood in for."""

    @asynccontextmanager
    async def __call__(self):
        yield _Uow()


def _o(kind: str, platform: str | None = None) -> dict[str, str | None]:
    return {"origin": kind, "origin_platform": platform}


class _Reads(ProjectionReads):
    """What the projections would read, answered here; deliveries recorded."""

    def __init__(self) -> None:
        super().__init__(_UowFactory())
        self.delivered: list[dict[str, object]] = []

    async def organization_of_pod(self, pod_id: UUID | None) -> UUID | None:
        return ORG if pod_id == POD else None

    async def pod_creator(self, pod_id: UUID) -> UUID | None:
        return BUILDER

    async def pod_member_count(self, pod_id: UUID) -> int:
        return 2

    async def organization_member_count(self, organization_id: UUID) -> int:
        return 2

    async def surface_of_conversation(self, conversation_id: UUID) -> UUID | None:
        return SURFACE

    async def deliver(self, **kwargs: object) -> None:
        self.delivered.append(kwargs)


@pytest.fixture
def sink():
    memory = MemorySink()
    # Strict: a contract violation fails the test instead of being logged.
    configure(memory, deployment="test", strict=True)
    yield memory
    configure(None)


@pytest.fixture
def reads() -> _Reads:
    return _Reads()


def _every_projected_event() -> list[DomainEvent]:
    """One committed event of every type a projection reads, pod-scoped where it can be."""
    return [
        UserSignedUpEvent(user_id=USER, email="a@b.c", method="google", **_o("WEB")),
        OrganizationCreatedEvent(
            organization_id=ORG, created_by_user_id=USER, **_o("WEB")
        ),
        OrganizationMemberAddedEvent(
            organization_id=ORG, user_id=USER, role="ADMIN", **_o("WEB")
        ),
        PodCreatedEvent(
            pod_id=POD,
            organization_id=ORG,
            creator_id=USER,
            name="p",
            source="person",
            **_o("WEB"),
        ),
        PodMemberAddedEvent(
            pod_id=POD, user_id=USER, role="EDITOR", email="a@b.c", **_o("WEB")
        ),
        DatastoreTableCreatedEvent(
            pod_id=POD, actor_id=USER, table_id=uuid4(), table_name="t", **_o("WEB")
        ),
        DatastoreFileCreatedEvent(
            pod_id=POD,
            actor_id=USER,
            file_id=uuid4(),
            path="/docs/report.pdf",
            size_bytes=123_456,
            **_o("WEB"),
        ),
        FunctionCreatedEvent(
            function_id=uuid4(), pod_id=POD, user_id=USER, **_o("WEB")
        ),
        AgentCreatedEvent(
            agent_id=uuid4(), pod_id=POD, user_id=USER, tool_count=3, **_o("WEB")
        ),
        ConversationStartedEvent(
            conversation_id=uuid4(), pod_id=POD, user_id=USER, **_o("WEB")
        ),
        # No organization on the event: the stop handler and the sweeps finish
        # runs like this, and the projection has to find it from the pod.
        AgentRunCompletedEvent(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            status=AgentRunStatus.COMPLETED,
            pod_id=POD,
            user_id=USER,
            started_at=_T0,
            occurred_at=_T0 + timedelta(seconds=3),
            **_o("SURFACE", "slack"),
        ),
        ScheduleCreated(
            schedule_id=uuid4(),
            schedule_type=ScheduleType.TIME,
            user_id=USER,
            pod_id=POD,
            config={},
            **_o("WEB"),
        ),
        ScheduleRunCompleted(
            schedule_id=uuid4(),
            schedule_type=ScheduleType.TIME,
            pod_id=POD,
            status="COMPLETED",
            user_id=USER,
            **_o("SCHEDULE"),
        ),
        WorkflowCreatedEvent(
            workflow_id=uuid4(), pod_id=POD, user_id=USER, node_count=4, **_o("WEB")
        ),
        WorkflowRunTerminalEvent(
            run_id=uuid4(),
            status=WorkflowRunStatus.COMPLETED,
            completed_at=_T0 + timedelta(minutes=3),
            started_at=_T0,
            workflow_id=uuid4(),
            pod_id=POD,
            user_id=USER,
            **_o("WORKFLOW"),
        ),
        SurfaceConnectedEvent(
            surface_id=SURFACE,
            pod_id=POD,
            platform="slack",
            connected_by_user_id=USER,
            **_o("WEB"),
        ),
        AppCreatedEvent(app_id=uuid4(), pod_id=POD, user_id=USER, **_o("WEB")),
        AppPublishedEvent(app_id=uuid4(), pod_id=POD, user_id=USER, **_o("CLI")),
        ConnectorConnectedEvent(
            connector_id="github", organization_id=ORG, user_id=USER, **_o("WEB")
        ),
        BundleExportedEvent(
            bundle_id=uuid4(), pod_id=POD, user_id=USER, resource_count=12, **_o("WEB")
        ),
        BundleImportCompletedEvent(
            import_id=uuid4(),
            bundle_id=uuid4(),
            pod_id=POD,
            user_id=USER,
            resource_count=5,
            **_o("IMPORT"),
        ),
        PodDeletedEvent(
            pod_id=POD,
            organization_id=ORG,
            deleted_by_user_id=USER,
            pod_created_at=_T0,
            occurred_at=_T0 + timedelta(days=40),
            **_o("WEB"),
        ),
    ]


_PROJECTIONS = (
    agent_events.on_agent_event,
    app_events.on_app_event,
    datastore_events.on_datastore_event,
    function_events.on_function_event,
    identity_events.on_identity_event,
    pod_events.on_pod_event,
    bundle_events.on_pod_bundle_event,
    schedule_events.on_schedule_event,
    surface_events.on_surface_event,
    workflow_events.on_workflow_event,
)


async def _project(reads: ProjectionReads, event: DomainEvent) -> None:
    payload = event.model_dump(mode="json")
    for handler in _PROJECTIONS:
        await handler(payload, reads=reads)
    await connector_events.on_connector_event(payload)


def _declares_organization(name: str) -> bool:
    return "organization" in ANALYTICS_CATALOG[name].groups


async def test_every_event_declaring_the_organization_group_carries_it(
    sink: MemorySink, reads: _Reads
) -> None:
    for event in _every_projected_event():
        await _project(reads, event)

    missing = [
        event.name
        for event in sink.events
        if _declares_organization(event.name)
        and event.groups.get("organization") != str(ORG)
    ]
    assert not missing, f"sent without their organization: {sorted(set(missing))}"

    # Not vacuous: every wired, organization-scoped event the projections own
    # was actually raised above.
    owned = {
        name
        for name in WIRED_EVENTS - _RAISED_ELSEWHERE
        if _declares_organization(name)
    }
    unexercised = owned - {event.name for event in sink.events}
    assert not unexercised, f"no event above exercised {sorted(unexercised)}"

    # And the activation each delivery-capable projection asked for names it.
    assert {call["via"] for call in reads.delivered} == {
        DeliveryVia.AGENT_RUN,
        DeliveryVia.SURFACE_MESSAGE,
        DeliveryVia.SCHEDULE_RUN,
        DeliveryVia.WORKFLOW_RUN,
    }
    assert all(call["organization_id"] == ORG for call in reads.delivered)


def test_a_connector_operation_carries_the_organization_its_plan_resolved(
    sink: MemorySink,
) -> None:
    plan = ResolvedConnectorExecution(
        connector_id="github",
        operation_execution_name="list_issues",
        provider="native",
        third_party_credentials={},
        payload={},
        organization_id=ORG,
        acting_user_id=USER,
    )
    with operation_execution_recorded(plan):
        pass

    (event,) = sink.events
    assert event.groups == {"organization": str(ORG)}


async def test_a_pods_organization_is_read_once_and_then_remembered() -> None:
    """A pod never moves organization, so the second ask costs nothing."""

    class _Counting(ProjectionReads):
        def __init__(self) -> None:
            super().__init__(_UowFactory())
            self.read: list[UUID] = []

        async def _read_pod_organization(self, pod_id: UUID) -> UUID | None:
            self.read.append(pod_id)
            return ORG

    forget_pod_organizations()
    counting = _Counting()
    try:
        assert await counting.organization_of_pod(POD) == ORG
        assert await counting.organization_of_pod(POD) == ORG
        assert await counting.organization_of_pod(None) is None
    finally:
        forget_pod_organizations()
    assert counting.read == [POD]


# -- what a finished run is allowed to claim -----------------------------------


def _surface_run(status: AgentRunStatus, **fields) -> AgentRunCompletedEvent:
    return AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=status,
        pod_id=POD,
        organization_id=ORG,
        user_id=USER,
        **_o("SURFACE", "slack"),
        **fields,
    )


@pytest.mark.parametrize("status", [AgentRunStatus.FAILED, AgentRunStatus.STOPPED])
async def test_a_run_that_did_not_complete_neither_activates_nor_answers(
    sink: MemorySink,
    reads: _Reads,
    status: AgentRunStatus,
) -> None:
    """It is still measured. It just is not an outcome: a pod whose only run
    failed has not delivered anything, and a surface whose question ended in an
    error was not answered."""
    await _project(reads, _surface_run(status))

    assert [event.name for event in sink.events] == ["agent_run.completed"]
    assert sink.events[0].properties["status"] == status.value
    assert reads.delivered == []


async def test_a_completed_surface_run_answers_and_may_activate(
    sink: MemorySink, reads: _Reads
) -> None:
    await _project(reads, _surface_run(AgentRunStatus.COMPLETED))

    assert [event.name for event in sink.events] == [
        "agent_run.completed",
        "surface.message_answered",
    ]
    assert [call["via"] for call in reads.delivered] == [
        DeliveryVia.AGENT_RUN,
        DeliveryVia.SURFACE_MESSAGE,
    ]


async def test_a_run_reports_how_much_it_did_when_it_is_known(
    sink: MemorySink, reads: _Reads
) -> None:
    await _project(
        reads,
        _surface_run(AgentRunStatus.COMPLETED, token_count=25_000, tool_call_count=0),
    )
    await _project(reads, _surface_run(AgentRunStatus.COMPLETED))

    counted, unknown = (e for e in sink.events if e.name == "agent_run.completed")
    assert counted.properties["token_count_bucket"] == "10000-100000"
    # Zero is an answer -- the run called no tools -- and not the first band.
    assert counted.properties["tool_call_count_bucket"] == "0"
    assert "token_count_bucket" not in unknown.properties
    assert "tool_call_count_bucket" not in unknown.properties


@pytest.mark.parametrize(
    ("origin", "counted"),
    [
        ("SCHEDULE", True),
        ("DATA_TRIGGER", True),
        ("CONNECTOR", True),
        (None, False),
        ("WEB", False),
    ],
)
async def test_schedule_runs_are_counted_by_what_fired_them(
    sink: MemorySink,
    reads: _Reads,
    origin: str | None,
    counted: bool,
) -> None:
    await _project(
        reads,
        ScheduleRunCompleted(
            schedule_id=uuid4(),
            schedule_type=ScheduleType.TIME,
            pod_id=POD,
            status="COMPLETED",
            user_id=USER,
            origin=origin,
        ),
    )
    assert [e.name for e in sink.events] == (
        ["schedule_run.completed"] if counted else []
    )
    assert [call["delegated_by_user_id"] for call in reads.delivered] == (
        [USER] if counted else []
    )


# -- declared properties are populated -----------------------------------------


async def test_the_declared_properties_are_filled_in(
    sink: MemorySink, reads: _Reads
) -> None:
    for event in _every_projected_event():
        await _project(reads, event)
    await _project(
        reads,
        WorkflowRunTerminalEvent(
            run_id=uuid4(),
            status=WorkflowRunStatus.COMPLETED,
            completed_at=_T0,
            pod_id=POD,
            waited_on_form=True,
            **_o("WORKFLOW"),
        ),
    )
    by_name: dict[str, list[dict[str, object]]] = {}
    for event in sink.events:
        by_name.setdefault(event.name, []).append(dict(event.properties))

    assert by_name["auth.signed_up"][0]["method"] == "google"
    assert by_name["pod.created"][0]["source"] == "person"
    assert by_name["pod.member_joined"][0]["member_count_bucket"] == "1-5"
    assert by_name["pod.deleted"][0]["age_days_bucket"] == "30-90d"
    assert by_name["document.added"][0]["size_bucket"] == "100kb-1mb"
    assert [p["waited_on_form"] for p in by_name["workflow_run.completed"]] == [
        False,
        True,
    ]


async def test_an_export_and_an_import_of_it_name_the_same_bundle(
    sink: MemorySink, reads: _Reads
) -> None:
    """The export's id travels inside the archive, so the import can name it.
    Each used to send its own job id, which joined nothing to nothing."""
    bundle_id = uuid4()
    await _project(
        reads,
        BundleExportedEvent(bundle_id=bundle_id, pod_id=POD, user_id=USER, **_o("WEB")),
    )
    await _project(
        reads,
        BundleImportCompletedEvent(
            import_id=uuid4(),
            bundle_id=bundle_id,
            pod_id=POD,
            user_id=USER,
            **_o("IMPORT"),
        ),
    )
    await _project(
        reads,
        BundleImportCompletedEvent(
            import_id=uuid4(), pod_id=POD, user_id=USER, **_o("IMPORT")
        ),
    )

    exported, imported, unknown = sink.events
    assert exported.properties["bundle_id"] == str(bundle_id)
    assert imported.properties["bundle_id"] == str(bundle_id)
    # A bundle no export produced has no bundle identity -- not the import's.
    assert "bundle_id" not in unknown.properties
