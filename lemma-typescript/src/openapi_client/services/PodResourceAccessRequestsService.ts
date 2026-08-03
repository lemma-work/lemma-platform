/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceAccessRequestCreateRequest } from '../models/ResourceAccessRequestCreateRequest.js';
import type { ResourceAccessRequestListResponse } from '../models/ResourceAccessRequestListResponse.js';
import type { ResourceAccessRequestResponse } from '../models/ResourceAccessRequestResponse.js';
import type { ResourceAccessRequestStatus } from '../models/ResourceAccessRequestStatus.js';
import type { ResourceType } from '../models/ResourceType.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodResourceAccessRequestsService {
    /**
     * List Resource Access Requests
     * @param podId
     * @param requestStatus
     * @returns ResourceAccessRequestListResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessRequestList(
        podId: string,
        requestStatus?: (ResourceAccessRequestStatus | null),
    ): CancelablePromise<ResourceAccessRequestListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/resource-access-requests',
            path: {
                'pod_id': podId,
            },
            query: {
                'request_status': requestStatus,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Request Access to a Resource
     * @param podId
     * @param requestBody
     * @returns ResourceAccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessRequestCreate(
        podId: string,
        requestBody: ResourceAccessRequestCreateRequest,
    ): CancelablePromise<ResourceAccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/resource-access-requests',
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
     * Get My Pending Request for a Resource
     * @param podId
     * @param resourceType
     * @param resourceId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static podResourceAccessRequestMe(
        podId: string,
        resourceType: ResourceType,
        resourceId: string,
    ): CancelablePromise<(ResourceAccessRequestResponse | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/resource-access-requests/me',
            path: {
                'pod_id': podId,
            },
            query: {
                'resource_type': resourceType,
                'resource_id': resourceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Approve a Resource Access Request
     * @param podId
     * @param requestId
     * @returns ResourceAccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessRequestApprove(
        podId: string,
        requestId: string,
    ): CancelablePromise<ResourceAccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/resource-access-requests/{request_id}/approve',
            path: {
                'pod_id': podId,
                'request_id': requestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reject a Resource Access Request
     * @param podId
     * @param requestId
     * @returns ResourceAccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static podResourceAccessRequestReject(
        podId: string,
        requestId: string,
    ): CancelablePromise<ResourceAccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/resource-access-requests/{request_id}/reject',
            path: {
                'pod_id': podId,
                'request_id': requestId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
