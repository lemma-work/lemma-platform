from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..openapi_client.api.notifications import (
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    notification_unread_count,
    pod_notify,
)
from ..openapi_client.models.notification_list_response import (
    NotificationListResponse,
)
from ..openapi_client.models.notification_unread_count_response import (
    NotificationUnreadCountResponse,
)
from ..openapi_client.models.notify_member_request import NotifyMemberRequest
from ..openapi_client.models.notify_member_response import NotifyMemberResponse
from .base import BoundResource, Resource, compact


class PodNotifications(BoundResource):
    """Reaching people from inside a pod.

    ``notify`` is the one to use: it picks whichever channel the person last
    talked to this pod on and always leaves the message in their Lemma inbox, so
    it cannot silently reach nobody. Compare ``pod.surfaces.send``, which targets
    one named surface and needs a thread they already started.
    """

    def notify(
        self,
        user_id: str | UUID,
        body: str,
        *,
        title: str | None = None,
    ) -> NotifyMemberResponse:
        return self._call(
            pod_notify,
            self._pod_uuid(),
            body={
                "user_id": str(user_id),
                "body": body,
                **compact({"title": title}),
            },
            body_model=NotifyMemberRequest,
        )


class Notifications(Resource):
    """The caller's own inbox, across every pod they belong to."""

    def list(
        self,
        *,
        pod_id: str | UUID | None = None,
        unread_only: bool = False,
        limit: int = 50,
        before: datetime | None = None,
    ) -> NotificationListResponse:
        return self._call(
            notification_list,
            **compact(
                {
                    "pod_id": str(pod_id) if pod_id else None,
                    "unread_only": unread_only or None,
                    "limit": limit,
                    "before": before,
                }
            ),
        )

    def unread_count(
        self, *, pod_id: str | UUID | None = None
    ) -> NotificationUnreadCountResponse:
        return self._call(
            notification_unread_count,
            **compact({"pod_id": str(pod_id) if pod_id else None}),
        )

    def mark_read(self, notification_id: str | UUID) -> None:
        self._call(notification_mark_read, str(notification_id))

    def mark_all_read(self, *, pod_id: str | UUID | None = None) -> None:
        self._call(
            notification_mark_all_read,
            **compact({"pod_id": str(pod_id) if pod_id else None}),
        )
