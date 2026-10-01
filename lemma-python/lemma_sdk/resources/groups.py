from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.agent_surfaces import (
    agent_group_get,
    agent_group_link,
    agent_group_list,
    agent_group_start,
    agent_group_timeline,
    agent_group_update,
)
from ..openapi_client.models.group_detail_response import GroupDetailResponse
from ..openapi_client.models.group_link_request import GroupLinkRequest
from ..openapi_client.models.group_link_response import GroupLinkResponse
from ..openapi_client.models.group_list_response import GroupListResponse
from ..openapi_client.models.group_response import GroupResponse
from ..openapi_client.models.group_start_request import GroupStartRequest
from ..openapi_client.models.group_timeline_response import GroupTimelineResponse
from ..openapi_client.models.group_update_request import GroupUpdateRequest
from .base import BoundResource, as_uuid


class PodGroups(BoundResource):
    """The group chats a pod's bots are in, as one place in the pod.

    People in the pod are answered in a group as themselves; people outside it
    are answered from what the pod made Public, and anything more goes to the
    member who answers for the group. ``start`` creates a WhatsApp group (a
    business number creates groups, it cannot join one) and comes back
    ``pending`` until WhatsApp confirms it. ``link`` returns a one-use, hour-long
    Telegram link that adds the bot to a group the caller picks. A question a
    group's people outside the pod are ``waiting`` on is answered through
    ``notifications.respond``.
    """

    def list(self) -> GroupListResponse:
        """Every group the pod's bots are in, most recently changed first."""
        return self._call(agent_group_list, self._pod_uuid())

    def get(self, group_id: str | UUID) -> GroupDetailResponse:
        """One group, with the people seen in it and what is waiting on you."""
        return self._call(agent_group_get, self._pod_uuid(), as_uuid(group_id))

    def timeline(
        self, group_id: str | UUID, *, limit: int = 60
    ) -> GroupTimelineResponse:
        """What was said in the group, oldest first (WhatsApp and Telegram)."""
        return self._call(
            agent_group_timeline, self._pod_uuid(), as_uuid(group_id), limit=limit
        )

    def start(self, request: GroupStartRequest | dict) -> GroupResponse:
        """Start a WhatsApp group with the pod's bot in it."""
        return self._call(
            agent_group_start,
            self._pod_uuid(),
            body=request,
            body_model=GroupStartRequest,
        )

    def link(self, surface_name: str) -> GroupLinkResponse:
        """A one-use, hour-long Telegram link that adds the bot to a group."""
        return self._call(
            agent_group_link,
            self._pod_uuid(),
            body={"surface_name": surface_name},
            body_model=GroupLinkRequest,
        )

    def update(
        self, group_id: str | UUID, request: GroupUpdateRequest | dict
    ) -> GroupResponse:
        """Switch outsiders on or off in one group, or take it over."""
        return self._call(
            agent_group_update,
            self._pod_uuid(),
            as_uuid(group_id),
            body=request,
            body_model=GroupUpdateRequest,
        )
