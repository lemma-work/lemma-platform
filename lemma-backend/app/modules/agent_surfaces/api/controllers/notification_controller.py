"""The recipient's inbox, and the one way to send to it.

Every endpoint here except the send is scoped to *your own* notifications. There
is no "list notifications for user X" — a notification body is often exactly the
kind of thing that should not be readable by a colleague, and an admin view is a
separate feature with a separate argument to make for it.

The lifecycle is exposed in full so the web app is a first-class responder and
not a viewer: answering here produces the same ``RESPONDED`` an agent-mediated
reply on Slack produces, which is what the asking run reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.composition.agent_notifications import deliver_replies_if_settled
from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import (
    PodContextDep,
    require_action,
    require_pod_membership,
)
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.api.dependencies import NotificationServiceDep
from app.modules.agent_surfaces.api.schemas import (
    NotificationListResponse,
    NotificationResponse,
    NotificationRespondRequest,
    NotificationUnreadCountResponse,
    NotifyMemberRequest,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationOriginKind,
    NotificationStatus,
)

router = APIRouter(prefix="/pods/{pod_id}/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    operation_id="notification.list",
    summary="List My Notifications",
    dependencies=[
        require_pod_membership("read notifications in this pod", enumerates=True)
    ],
    description=(
        "Notifications addressed to the current user in this pod, newest first. "
        "Filter with `status` (repeatable). Each item carries everything needed "
        "to render its action: `awaiting_response` decides whether to offer one, "
        "and `responds_through_action` decides whether it is a free-text reply "
        "or the form described by `action`."
    ),
)
async def list_notifications(
    pod_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
    status_filter: list[NotificationStatus] | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    page_token: str | None = None,
) -> NotificationListResponse:
    del ctx
    items, next_cursor = await service.notifications.list_for_recipient(
        pod_id=pod_id,
        recipient_user_id=user.id,
        statuses=status_filter,
        limit=limit,
        cursor=parse_uuid_page_token(page_token),
    )
    return NotificationListResponse(
        items=[NotificationResponse.from_entity(item) for item in items],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/unread-count",
    response_model=NotificationUnreadCountResponse,
    operation_id="notification.unread_count",
    summary="Count My Unread Notifications",
    dependencies=[
        require_pod_membership("read notifications in this pod", enumerates=True)
    ],
    description=(
        "Unread, not unanswered. A notification you have read but not yet acted "
        "on has stopped being new."
    ),
)
async def unread_count(
    pod_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationUnreadCountResponse:
    del ctx
    return NotificationUnreadCountResponse(
        unread=await service.notifications.count_unread(
            pod_id=pod_id, recipient_user_id=user.id
        )
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    operation_id="notification.mark_read",
    dependencies=[require_pod_membership("act on notifications in this pod")],
    summary="Mark Notification Read",
)
async def mark_read(
    pod_id: UUID,
    notification_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationResponse:
    del ctx
    return NotificationResponse.from_entity(
        await service.mark_read(
            pod_id=pod_id, notification_id=notification_id, user_id=user.id
        )
    )


@router.post(
    "/read-all",
    response_model=NotificationUnreadCountResponse,
    operation_id="notification.mark_all_read",
    dependencies=[require_pod_membership("act on notifications in this pod")],
    summary="Mark All My Notifications Read",
    description="Returns the remaining unread count, which is always zero.",
)
async def mark_all_read(
    pod_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationUnreadCountResponse:
    del ctx
    await service.notifications.mark_all_read(pod_id=pod_id, recipient_user_id=user.id)
    return NotificationUnreadCountResponse(unread=0)


@router.post(
    "/{notification_id}/respond",
    response_model=NotificationResponse,
    operation_id="notification.respond",
    dependencies=[require_pod_membership("act on notifications in this pod")],
    summary="Respond To A Notification",
    description=(
        "Answer a notification from the app. Produces the same `RESPONDED` an "
        "agent-mediated reply on a chat surface produces, so the asking run "
        "sees it either way.\n\n"
        "Returns 409 when the notification is answered by completing its "
        "`action` instead — a workflow form is submitted through the workflow "
        "run endpoint, where it is validated against the node's schema. It also "
        "returns 409 if somebody already answered it, rather than overwriting "
        "an answer that may already have been acted on."
    ),
)
async def respond_to_notification(
    pod_id: UUID,
    notification_id: UUID,
    request: NotificationRespondRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationResponse:
    del ctx
    notification = await service.respond(
        pod_id=pod_id,
        notification_id=notification_id,
        responder_user_id=user.id,
        summary=request.summary,
        data=request.data,
    )

    async def _deliver_replies() -> None:
        await deliver_replies_if_settled(notification)

    # After the request's commit, not here. The delivery reads whether anything
    # is still outstanding, and this answer is not yet visible to another
    # session — running it now would count the row it just closed and leave the
    # asker with nothing. `get_uow` commits in its teardown, before the response
    # goes out.
    service.uow.after_commit(_deliver_replies)
    return NotificationResponse.from_entity(notification)


@router.post(
    "/{notification_id}/acknowledge",
    response_model=NotificationResponse,
    operation_id="notification.acknowledge",
    dependencies=[require_pod_membership("act on notifications in this pod")],
    summary="Acknowledge A Notification",
    description=(
        "Dismiss a notification that asked for nothing. Returns 409 when a "
        "response is owed — dismissing a question is not answering it."
    ),
)
async def acknowledge_notification(
    pod_id: UUID,
    notification_id: UUID,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationResponse:
    del ctx
    return NotificationResponse.from_entity(
        await service.acknowledge(
            pod_id=pod_id, notification_id=notification_id, user_id=user.id
        )
    )


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="notification.send",
    summary="Notify A Pod Member",
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
    description=(
        "Reach a pod member on whichever surface they actually use, leaving a "
        "copy in their Lemma inbox either way.\n\n"
        "Gated on `conversation.write` rather than an editor permission: this "
        "opens a conversation and writes a message into it, which is exactly "
        "that grant. Requiring `agent.update` is what left the older "
        "`surface.send` endpoint with no caller in the product.\n\n"
        "A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a "
        "failure — the notification exists and the inbox has it. Read "
        "`undeliverable_reason` to tell the user what to do about it."
    ),
)
async def notify_member(
    pod_id: UUID,
    request: NotifyMemberRequest,
    user: CurrentUser,
    ctx: PodContextDep,
    service: NotificationServiceDep,
) -> NotificationResponse:
    del ctx
    recipient_user_id = await service.membership.resolve_pod_recipient(
        pod_id=pod_id, reference=request.recipient
    )
    if recipient_user_id is None:
        # 404 rather than 422: from the caller's side an id that names nobody in
        # this pod and an id that names somebody outside it are the same fact,
        # and distinguishing them would confirm that the person exists.
        raise HTTPException(
            status_code=404,
            detail="No pod member matches that recipient.",
        )

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=request.expires_in_seconds)
        if request.expires_in_seconds
        else None
    )
    return NotificationResponse.from_entity(
        await service.notify(
            pod_id=pod_id,
            recipient_user_id=recipient_user_id,
            title=request.title,
            body=request.body,
            origin_kind=NotificationOriginKind.API,
            actor_user_id=user.id,
            background_instruction=request.background_instruction,
            expects_response=request.expects_response,
            expires_at=expires_at,
        )
    )
