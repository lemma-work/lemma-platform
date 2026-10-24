"""Wire shapes of the organization overview: what waits on a member, what moved
in each pod, every app, and when they last looked at Home.

Each answers for every pod the member may open in one organization, so a client
draws an overview of all of them without a request per pod.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.core.api.schemas import BaseSchema
from app.modules.agent.contracts.conversation_summaries import ConversationOrigin
from app.modules.identity.services.organization_needs_you import NeedsYouKind
from app.modules.identity.services.organization_recents import AutomationKind


class NeedsYouItemResponse(BaseSchema):
    """One thing waiting on the member, and where to answer it.

    Exactly one of ``conversation_id``, ``workflow_run_id`` and ``channel_id``
    is set, by kind: a conversation for QUESTION, APPROVAL and PERMISSION; a
    workflow run for FORM and DECISION; a contact's channel for CONTACT.
    """

    key: str = Field(
        description=(
            "Stable across reads: `conversation:<id>`, `wait:<id>` or "
            "`contact:<channel id>`."
        )
    )
    kind: NeedsYouKind
    pod_id: UUID
    pod_name: str
    title: str | None = Field(
        default=None,
        description=(
            "What is asked. QUESTION/APPROVAL/PERMISSION: the question or what "
            "the agent wants to do, else the conversation's title. FORM/"
            "DECISION: the decision's question or the form's title, else the "
            "workflow's name. CONTACT: the contact's name."
        ),
    )
    detail: str | None = Field(
        default=None,
        description=(
            "Where it is. The conversation's title when `title` is the "
            "question; the workflow's name; or why the contact asked for a "
            "person."
        ),
    )
    since: datetime = Field(description="When it started waiting.")
    conversation_id: UUID | None = None
    tool_call_id: str | None = Field(
        default=None,
        description=(
            "A conversation's pause, as the approval id its decision endpoint takes."
        ),
    )
    workflow_run_id: UUID | None = None
    channel_id: UUID | None = None


class NeedsYouResponse(BaseSchema):
    items: list[NeedsYouItemResponse] = Field(
        description="Oldest first: what has waited longest is listed first."
    )
    limit: int
    truncated: bool = Field(
        description="More was waiting than `limit`; the oldest are listed."
    )


class RecentChatResponse(BaseSchema):
    conversation_id: UUID
    title: str | None = None
    origin: ConversationOrigin = Field(
        description=(
            "`chat` or `channel` -- or, for a conversation waiting on its "
            "owner, whatever started it."
        )
    )
    status: str | None = Field(
        default=None, description="RUNNING while an agent works in it."
    )
    agent_id: UUID | None = None
    last_activity_at: datetime
    waiting_on_you: bool
    platform: str | None = Field(
        default=None,
        description="The platform a channel's conversation came in on.",
    )
    channel_name: str | None = None


class AutomationResponse(BaseSchema):
    kind: AutomationKind
    id: UUID = Field(description="The workflow's or the schedule's id.")
    name: str | None = None
    run_count: int = Field(
        description="Runs of the workflow, or fires of the schedule, in the window."
    )
    latest_run_id: UUID = Field(
        description="The newest workflow run, or the newest schedule fire."
    )
    latest_status: str
    latest_at: datetime
    workflow_run_id: UUID | None = Field(
        default=None,
        description=(
            "The workflow run to open for the newest: the workflow's own, or "
            "the one a schedule started."
        ),
    )
    conversation_id: UUID | None = Field(
        default=None,
        description="The conversation a schedule's agent ran in, for the newest fire.",
    )


class DayCountResponse(BaseSchema):
    day: date = Field(description="A calendar day in the requested time zone.")
    by_agent: int = Field(
        description=(
            "Conversations last active that day that nobody typed the first "
            "message of: a schedule, a workflow, a notification, a task, or "
            "another pod's ask."
        )
    )
    by_people: int = Field(description="Conversations a person started.")


class PodRecentsResponse(BaseSchema):
    pod_id: UUID
    pod_name: str
    chats: list[RecentChatResponse] = Field(
        description="The member's latest chats, newest first."
    )
    automations: list[AutomationResponse] = Field(
        description=(
            "Workflows and schedules that ran in the window, one each, the "
            "most recent first."
        )
    )
    activity: list[DayCountResponse] = Field(
        description="One entry per day of the window, oldest first, zeros included."
    )


class OrganizationRecentsResponse(BaseSchema):
    organization_id: UUID
    timezone: str
    days: int
    since: datetime = Field(description="When the window's first day began.")
    pods: list[PodRecentsResponse]


class OrganizationAppResponse(BaseSchema):
    id: UUID
    pod_id: UUID
    pod_name: str
    name: str
    description: str | None = None
    # None where the deployment serves no app host -- see
    # `apps.domain.entities.public_app_url`.
    url: str | None = None
    status: str
    visibility: str
    updated_at: datetime


class OrganizationAppListResponse(BaseSchema):
    items: list[OrganizationAppResponse] = Field(
        description="Every app the member may open, most recently updated first."
    )


class HomeSeenResponse(BaseSchema):
    seen_at: datetime | None = Field(
        default=None,
        description="When the member last looked at Home; null until first marked.",
    )
    previous_seen_at: datetime | None = Field(
        default=None,
        description="What it was before this write. On a read, the same as `seen_at`.",
    )


class HomeSeenRequest(BaseSchema):
    seen_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "The moment to mark, with its offset -- typically when the page "
            "loaded, so what arrived while it was open still counts as new. "
            "Omitted, now. A moment in the future is read as now, and the mark "
            "never moves back."
        ),
    )
