"""One recorded scenario of every domain event analytics projects.

Shared by the parity test and by the recording that produced its golden file:
the golden (``analytics_parity_golden.json``) was written by feeding these
events, committed through a real unit of work, into the eleven FastStream
``analytics-*`` consumers that existed before the after-commit sink replaced
them. The parity test commits the same events and asserts the sink emits the
same analytics, byte for byte after normalisation.

Ids are fixed (``uuid5`` of a label) except the rows the database has to hold
for the projections that read back -- the pod (its creator, its delivery
marker), the organization (its member count), the person and a conversation
(the run-scope fallback) -- which are created per run and normalised to
placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.analytics import CapturedEvent
from app.core.domain.events import DomainEvent
from app.modules.agent.domain.events import (
    AgentCreatedEvent,
    AgentRunCompletedEvent,
    ConversationStartedEvent,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent_surfaces.domain.events import SurfaceConnectedEvent
from app.modules.apps.domain.events import AppCreatedEvent, AppPublishedEvent
from app.modules.connectors.domain.events import ConnectorConnectedEvent
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

_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def fixed(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"analytics-parity:{label}")


@dataclass(frozen=True)
class ScenarioRows:
    """The rows created per run, and what they normalise to."""

    pod_id: UUID
    organization_id: UUID
    user_id: UUID
    conversation_id: UUID

    def placeholders(self) -> dict[str, str]:
        return {
            str(self.pod_id): "<pod>",
            str(self.organization_id): "<org>",
            str(self.user_id): "<user>",
            str(self.conversation_id): "<conversation>",
        }


def _o(kind: str | None, platform: str | None = None) -> dict[str, Any]:
    return {"origin": kind, "origin_platform": platform}


def scenario_events(rows: ScenarioRows) -> list[DomainEvent]:
    """Every wired source event type, with the origins and branches that matter."""
    pod, org, user, conv = (
        rows.pod_id,
        rows.organization_id,
        rows.user_id,
        rows.conversation_id,
    )
    return [
        UserSignedUpEvent(user_id=user, email="a@b.c", **_o("WEB")),
        OrganizationCreatedEvent(
            organization_id=org, created_by_user_id=user, **_o("WEB")
        ),
        OrganizationCreatedEvent(organization_id=fixed("org-2"), **_o(None)),
        OrganizationMemberAddedEvent(
            organization_id=org, user_id=user, role="ADMIN", **_o("CLI")
        ),
        PodCreatedEvent(
            pod_id=pod, organization_id=org, creator_id=user, name="p", **_o("WEB")
        ),
        PodMemberAddedEvent(
            pod_id=pod, user_id=user, role="EDITOR", email="a@b.c", **_o("SDK")
        ),
        DatastoreTableCreatedEvent(
            pod_id=pod,
            actor_id=user,
            table_id=fixed("table"),
            table_name="t",
            **_o("MCP_POD"),
        ),
        DatastoreFileCreatedEvent(
            pod_id=pod,
            actor_id=None,
            file_id=fixed("file"),
            path="/docs/report.pdf",
            size_bytes=123_456,
            **_o("IMPORT"),
        ),
        FunctionCreatedEvent(
            function_id=fixed("function"), pod_id=pod, user_id=user, **_o("WEB")
        ),
        AgentCreatedEvent(
            agent_id=fixed("agent"), pod_id=pod, user_id=user, tool_count=7, **_o("WEB")
        ),
        ConversationStartedEvent(
            conversation_id=conv,
            pod_id=pod,
            user_id=user,
            agent_id=fixed("agent"),
            **_o("WEB"),
        ),
        ConversationStartedEvent(
            conversation_id=fixed("child-conv"),
            pod_id=pod,
            user_id=user,
            parent_id=conv,
            **_o("WEB"),
        ),
        # Scope on the event: no read.
        AgentRunCompletedEvent(
            conversation_id=conv,
            agent_run_id=fixed("run-1"),
            status=AgentRunStatus.COMPLETED,
            pod_id=pod,
            organization_id=org,
            agent_id=fixed("agent"),
            user_id=user,
            started_at=_T0,
            occurred_at=_T0 + timedelta(seconds=42),
            **_o("WEB"),
        ),
        # Scope read back from the conversation row.
        AgentRunCompletedEvent(
            conversation_id=conv,
            agent_run_id=fixed("run-2"),
            status=AgentRunStatus.FAILED,
            **_o("SCHEDULE"),
        ),
        # A surface-origin run in a conversation with no surface link.
        AgentRunCompletedEvent(
            conversation_id=conv,
            agent_run_id=fixed("run-3"),
            status=AgentRunStatus.COMPLETED,
            pod_id=pod,
            organization_id=org,
            user_id=user,
            started_at=_T0,
            occurred_at=_T0 + timedelta(seconds=3),
            **_o("SURFACE", "slack"),
        ),
        ScheduleCreated(
            schedule_id=fixed("schedule"),
            schedule_type=ScheduleType.TIME,
            user_id=user,
            pod_id=pod,
            config={},
            **_o("WEB"),
        ),
        ScheduleRunCompleted(
            schedule_id=fixed("schedule"),
            schedule_type=ScheduleType.TIME,
            pod_id=pod,
            status="completed",
            user_id=user,
            **_o("SCHEDULE"),
        ),
        ScheduleRunCompleted(
            schedule_id=fixed("schedule"),
            schedule_type=ScheduleType.TIME,
            pod_id=pod,
            status="completed",
            **_o("WEB"),
        ),
        WorkflowCreatedEvent(
            workflow_id=fixed("workflow"),
            pod_id=pod,
            user_id=user,
            node_count=4,
            **_o("WEB"),
        ),
        WorkflowRunTerminalEvent(
            run_id=fixed("wf-run"),
            status=WorkflowRunStatus.COMPLETED,
            completed_at=_T0 + timedelta(minutes=3),
            started_at=_T0,
            workflow_id=fixed("workflow"),
            pod_id=pod,
            user_id=user,
            **_o("WORKFLOW"),
        ),
        WorkflowRunTerminalEvent(
            run_id=fixed("wf-run-2"),
            status=WorkflowRunStatus.FAILED,
            completed_at=_T0,
            **_o("WORKFLOW"),
        ),
        SurfaceConnectedEvent(
            surface_id=fixed("surface"),
            pod_id=pod,
            platform="slack",
            agent_id=fixed("agent"),
            connected_by_user_id=user,
            **_o("WEB"),
        ),
        AppCreatedEvent(app_id=fixed("app"), pod_id=pod, user_id=user, **_o("WEB")),
        AppPublishedEvent(app_id=fixed("app"), pod_id=pod, **_o("CLI")),
        ConnectorConnectedEvent(
            connector_id="github", organization_id=org, user_id=user, **_o("WEB")
        ),
        BundleExportedEvent(
            bundle_id=fixed("export"),
            pod_id=pod,
            user_id=user,
            resource_count=12,
            **_o("WEB"),
        ),
        # An import of the export above: the archive carried its id.
        BundleImportCompletedEvent(
            import_id=fixed("import"),
            bundle_id=fixed("export"),
            pod_id=pod,
            user_id=user,
            resource_count=5,
            **_o("IMPORT"),
        ),
        PodDeletedEvent(
            pod_id=fixed("pod-2"),
            organization_id=org,
            deleted_by_user_id=user,
            **_o("WEB"),
        ),
    ]


def normalise(
    captured: list[CapturedEvent], rows: ScenarioRows
) -> list[dict[str, Any]]:
    """Captured events as plain data, with the per-run ids replaced."""
    names = rows.placeholders()

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            return names.get(value, value)
        return value

    return [
        {
            "name": event.name,
            "distinct_id": scrub(event.distinct_id),
            "properties": {k: scrub(v) for k, v in sorted(event.properties.items())},
            "groups": {k: scrub(v) for k, v in sorted(event.groups.items())},
        }
        for event in captured
    ]
