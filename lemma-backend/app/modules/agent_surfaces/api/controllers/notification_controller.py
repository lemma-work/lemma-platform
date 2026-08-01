"""The in-app inbox (``/notifications``).

User-scoped, like ``/surfaces/me``: an inbox belongs to a person, not to a pod,
because the whole point is one place to look. Pod filtering is a query
parameter, not a path segment.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.agent_surfaces.api.schemas import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (
    NotificationRepository,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    operation_id="notification.list",
)
async def list_notifications(
    user: CurrentUser,
    uow: UoWDep,
    pod_id: UUID | None = None,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
) -> NotificationListResponse:
    """The current user's notifications, newest first."""
    repository = NotificationRepository(uow)
    items = await repository.list_for_user(
        user_id=user.id,
        pod_id=pod_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    )
    unread = await repository.unread_count(user_id=user.id, pod_id=pod_id)
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(item.model_dump()) for item in items],
        unread_count=unread,
    )


@router.get(
    "/unread-count",
    response_model=NotificationUnreadCountResponse,
    operation_id="notification.unread_count",
)
async def unread_count(
    user: CurrentUser,
    uow: UoWDep,
    pod_id: UUID | None = None,
) -> NotificationUnreadCountResponse:
    """Just the badge number — the one query on the render hot path."""
    count = await NotificationRepository(uow).unread_count(
        user_id=user.id, pod_id=pod_id
    )
    return NotificationUnreadCountResponse(unread_count=count)


@router.post(
    "/{notification_id}/read",
    operation_id="notification.mark_read",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_read(
    notification_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
) -> Response:
    """Mark one notification read.

    Scoped to the caller, so a notification id alone is never enough to reach
    into somebody else's inbox. Already-read is success, not a 404 — marking
    twice is what a double click looks like.
    """
    repository = NotificationRepository(uow)
    changed = await repository.mark_read(
        notification_id=notification_id, user_id=user.id
    )
    if not changed:
        existing = await repository.list_for_user(user_id=user.id, limit=200)
        if not any(item.id == notification_id for item in existing):
            raise HTTPException(status_code=404, detail="Notification not found.")
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/read-all",
    operation_id="notification.mark_all_read",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_all_read(
    user: CurrentUser,
    uow: UoWDep,
    pod_id: UUID | None = None,
) -> Response:
    await NotificationRepository(uow).mark_all_read(user_id=user.id, pod_id=pod_id)
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
