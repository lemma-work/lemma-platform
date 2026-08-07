from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.notifications import (
    notification_acknowledge,
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    notification_respond,
    notification_send,
    notification_unread_count,
)
from ..openapi_client.models.notification_list_response import (
    NotificationListResponse,
)
from ..openapi_client.models.notification_respond_request import (
    NotificationRespondRequest,
)
from ..openapi_client.models.notification_response import NotificationResponse
from ..openapi_client.models.notification_status import NotificationStatus
from ..openapi_client.models.notification_unread_count_response import (
    NotificationUnreadCountResponse,
)
from ..openapi_client.models.notify_member_request import NotifyMemberRequest
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


class PodNotifications(BoundResource):
    """Things the pod has asked the current user for, and how to answer them.

    Every method except :meth:`send` is scoped to the caller's own notifications
    — there is no way to read somebody else's.

    Two states, read independently. ``status`` is about the person (``OPEN``,
    ``RESPONDED``, ``ACKNOWLEDGED``, ``EXPIRED``, ``CANCELLED``);
    ``delivery_status`` is about the channel (``DELIVERED``, ``UNDELIVERABLE``,
    ``FAILED``). ``UNDELIVERABLE`` is not an error — no chat app or mailbox could
    carry it, and it is in the inbox regardless.
    """

    def list(
        self,
        *,
        status: list[NotificationStatus] | None = None,
        limit: int = 50,
        page_token: str | None = None,
    ) -> NotificationListResponse:
        """My notifications in this pod, newest first."""
        return self._call(
            notification_list,
            self._pod_uuid(),
            status=status if status is not None else UNSET,
            limit=limit,
            page_token=page_token if page_token is not None else UNSET,
        )

    def unread_count(self) -> NotificationUnreadCountResponse:
        """How many I have not read.

        Keyed on being read, not answered — a badge that only clears when you
        finish the work is a badge people stop looking at.
        """
        return self._call(notification_unread_count, self._pod_uuid())

    def mark_read(self, notification_id: str | UUID) -> NotificationResponse:
        return self._call(
            notification_mark_read, self._pod_uuid(), as_uuid(notification_id)
        )

    def mark_all_read(self) -> NotificationUnreadCountResponse:
        return self._call(notification_mark_all_read, self._pod_uuid())

    def respond(
        self,
        notification_id: str | UUID,
        *,
        summary: str,
        data: dict | None = None,
    ) -> NotificationResponse:
        """Answer one.

        Produces the same ``RESPONDED`` an agent-mediated reply on a chat surface
        produces, so the run that asked reads one thing either way.

        Raises on 409 when the notification is answered by completing its
        ``action`` instead (a workflow form, submitted through
        ``workflows.runs.submit_form`` where it is validated against the node's
        schema), and when somebody has already answered it.
        """
        return self._call(
            notification_respond,
            self._pod_uuid(),
            as_uuid(notification_id),
            body=NotificationRespondRequest(summary=summary, data=data or UNSET),
        )

    def acknowledge(self, notification_id: str | UUID) -> NotificationResponse:
        """Dismiss one that asked for nothing. 409 when a response is owed."""
        return self._call(
            notification_acknowledge, self._pod_uuid(), as_uuid(notification_id)
        )

    def send(self, request: NotifyMemberRequest) -> NotificationResponse:
        """Reach a pod member wherever they are, leaving a copy in their inbox.

        A 201 whose ``delivery_status`` is ``UNDELIVERABLE`` succeeded; read
        ``undeliverable_reason`` to tell the user what to do about it.
        """
        return self._call(notification_send, self._pod_uuid(), body=request)
