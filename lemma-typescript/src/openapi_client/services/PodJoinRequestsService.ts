/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodJoinRequestApproveRequest } from '../models/PodJoinRequestApproveRequest.js';
import type { PodJoinRequestCreateResponse } from '../models/PodJoinRequestCreateResponse.js';
import type { PodJoinRequestListResponse } from '../models/PodJoinRequestListResponse.js';
import type { PodJoinRequestStatus } from '../models/PodJoinRequestStatus.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodJoinRequestsService {
    /**
     * List Pod Join Requests
     * List join requests for a pod
     * @param podId
     * @param statusFilter
     * @param limit
     * @param pageToken
     * @returns PodJoinRequestListResponse Successful Response
     * @throws ApiError
     */
    public static podJoinRequestList(
        podId: string,
        statusFilter?: (PodJoinRequestStatus | null),
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<PodJoinRequestListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/join-requests',
            path: {
                'pod_id': podId,
            },
            query: {
                'status_filter': statusFilter,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Pod Join Request
     * Create a join request for the current user to access this pod
     * @param podId
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    public static podJoinRequestCreate(
        podId: string,
    ): CancelablePromise<PodJoinRequestCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/join-requests',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Withdraw My Pod Join Request
     * Take back the current user's pending join request for this pod
     * @param podId
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    public static podJoinRequestWithdraw(
        podId: string,
    ): CancelablePromise<PodJoinRequestCreateResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/join-requests/me',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get My Pod Join Request
     * Get the current user's pending join request for this pod, or the last one if it was rejected
     * @param podId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static podJoinRequestMe(
        podId: string,
    ): CancelablePromise<(PodJoinRequestCreateResponse | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/join-requests/me',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Approve Pod Join Request
     * Approve a pending pod join request and add user to org/pod
     * @param podId
     * @param joinRequestId
     * @param requestBody
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    public static podJoinRequestApprove(
        podId: string,
        joinRequestId: string,
        requestBody: PodJoinRequestApproveRequest,
    ): CancelablePromise<PodJoinRequestCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/join-requests/{join_request_id}/approve',
            path: {
                'pod_id': podId,
                'join_request_id': joinRequestId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reject Pod Join Request
     * Turn down a pending pod join request; the asker is told by email
     * @param podId
     * @param joinRequestId
     * @returns PodJoinRequestCreateResponse Successful Response
     * @throws ApiError
     */
    public static podJoinRequestReject(
        podId: string,
        joinRequestId: string,
    ): CancelablePromise<PodJoinRequestCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/join-requests/{join_request_id}/reject',
            path: {
                'pod_id': podId,
                'join_request_id': joinRequestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
