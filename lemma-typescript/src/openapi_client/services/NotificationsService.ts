/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotificationListResponse } from '../models/NotificationListResponse.js';
import type { NotificationUnreadCountResponse } from '../models/NotificationUnreadCountResponse.js';
import type { NotifyMemberRequest } from '../models/NotifyMemberRequest.js';
import type { NotifyMemberResponse } from '../models/NotifyMemberResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class NotificationsService {
    /**
     * List Notifications
     * The current user's notifications, newest first.
     * @param podId
     * @param unreadOnly
     * @param limit
     * @param before
     * @returns NotificationListResponse Successful Response
     * @throws ApiError
     */
    public static notificationList(
        podId?: (string | null),
        unreadOnly: boolean = false,
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<NotificationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/notifications',
            query: {
                'pod_id': podId,
                'unread_only': unreadOnly,
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark All Read
     * @param podId
     * @returns void
     * @throws ApiError
     */
    public static notificationMarkAllRead(
        podId?: (string | null),
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/notifications/read-all',
            query: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Unread Count
     * Just the badge number — the one query on the render hot path.
     * @param podId
     * @returns NotificationUnreadCountResponse Successful Response
     * @throws ApiError
     */
    public static notificationUnreadCount(
        podId?: (string | null),
    ): CancelablePromise<NotificationUnreadCountResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/notifications/unread-count',
            query: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Read
     * Mark one notification read.
     *
     * Scoped to the caller, so a notification id alone is never enough to reach
     * into somebody else's inbox. Already-read is success, not a 404 — marking
     * twice is what a double click looks like.
     * @param notificationId
     * @returns void
     * @throws ApiError
     */
    public static notificationMarkRead(
        notificationId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/notifications/{notification_id}/read',
            path: {
                'notification_id': notificationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Notify Pod Member
     * Tell a pod member something, letting Lemma choose where to reach them.
     *
     * Unlike ``surfaces/{name}/send``, which targets one named surface and needs a
     * thread the person already started, this picks whichever channel they last
     * used and always leaves the message in their Lemma inbox — so it cannot
     * silently reach nobody.
     *
     * Gated on ``conversation.write`` rather than ``agent.update``: sending
     * somebody a message is not an act of editing agents, and requiring an editor
     * permission is what kept functions and apps from using this at all.
     * @param podId
     * @param requestBody
     * @returns NotifyMemberResponse Successful Response
     * @throws ApiError
     */
    public static podNotify(
        podId: string,
        requestBody: NotifyMemberRequest,
    ): CancelablePromise<NotifyMemberResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/notify',
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
}
