/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotificationListResponse } from '../models/NotificationListResponse.js';
import type { NotificationRespondRequest } from '../models/NotificationRespondRequest.js';
import type { NotificationResponse } from '../models/NotificationResponse.js';
import type { NotificationStatus } from '../models/NotificationStatus.js';
import type { NotificationUnreadCountResponse } from '../models/NotificationUnreadCountResponse.js';
import type { NotifyMemberRequest } from '../models/NotifyMemberRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class NotificationsService {
    /**
     * List My Notifications
     * Notifications addressed to the current user in this pod, newest first. Filter with `status` (repeatable). Each item carries everything needed to render its action: `awaiting_response` decides whether to offer one, and `responds_through_action` decides whether it is a free-text reply or the form described by `action`.
     * @param podId
     * @param status
     * @param limit
     * @param pageToken
     * @returns NotificationListResponse Successful Response
     * @throws ApiError
     */
    public static notificationList(
        podId: string,
        status?: (Array<NotificationStatus> | null),
        limit: number = 50,
        pageToken?: (string | null),
    ): CancelablePromise<NotificationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/notifications',
            path: {
                'pod_id': podId,
            },
            query: {
                'status': status,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Notify A Pod Member
     * Reach a pod member on whichever surface they actually use, leaving a copy in their Lemma inbox either way.
     *
     * Gated on `conversation.write` rather than an editor permission: this opens a conversation and writes a message into it, which is exactly that grant. Requiring `agent.update` is what left the older `surface.send` endpoint with no caller in the product.
     *
     * A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a failure — the notification exists and the inbox has it. Read `undeliverable_reason` to tell the user what to do about it.
     * @param podId
     * @param requestBody
     * @returns NotificationResponse Successful Response
     * @throws ApiError
     */
    public static notificationSend(
        podId: string,
        requestBody: NotifyMemberRequest,
    ): CancelablePromise<NotificationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notifications',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark All My Notifications Read
     * Returns the remaining unread count, which is always zero.
     * @param podId
     * @returns NotificationUnreadCountResponse Successful Response
     * @throws ApiError
     */
    public static notificationMarkAllRead(
        podId: string,
    ): CancelablePromise<NotificationUnreadCountResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notifications/read-all',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Count My Unread Notifications
     * Unread, not unanswered. A notification you have read but not yet acted on has stopped being new.
     * @param podId
     * @returns NotificationUnreadCountResponse Successful Response
     * @throws ApiError
     */
    public static notificationUnreadCount(
        podId: string,
    ): CancelablePromise<NotificationUnreadCountResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/notifications/unread-count',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Acknowledge A Notification
     * Dismiss a notification that asked for nothing. Returns 409 when a response is owed — dismissing a question is not answering it.
     * @param podId
     * @param notificationId
     * @returns NotificationResponse Successful Response
     * @throws ApiError
     */
    public static notificationAcknowledge(
        podId: string,
        notificationId: string,
    ): CancelablePromise<NotificationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notifications/{notification_id}/acknowledge',
            path: {
                'pod_id': podId,
                'notification_id': notificationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Notification Read
     * @param podId
     * @param notificationId
     * @returns NotificationResponse Successful Response
     * @throws ApiError
     */
    public static notificationMarkRead(
        podId: string,
        notificationId: string,
    ): CancelablePromise<NotificationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notifications/{notification_id}/read',
            path: {
                'pod_id': podId,
                'notification_id': notificationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Respond To A Notification
     * Answer a notification from the app. Produces the same `RESPONDED` an agent-mediated reply on a chat surface produces, so the asking run sees it either way.
     *
     * Returns 409 when the notification is answered by completing its `action` instead — a workflow form is submitted through the workflow run endpoint, where it is validated against the node's schema. It also returns 409 if somebody already answered it, rather than overwriting an answer that may already have been acted on.
     * @param podId
     * @param notificationId
     * @param requestBody
     * @returns NotificationResponse Successful Response
     * @throws ApiError
     */
    public static notificationRespond(
        podId: string,
        notificationId: string,
        requestBody: NotificationRespondRequest,
    ): CancelablePromise<NotificationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notifications/{notification_id}/respond',
            path: {
                'pod_id': podId,
                'notification_id': notificationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
