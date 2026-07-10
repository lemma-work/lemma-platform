/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodCreateRequest } from '../models/PodCreateRequest.js';
import type { PodListResponse } from '../models/PodListResponse.js';
import type { PodMemberResponse } from '../models/PodMemberResponse.js';
import type { PodResponse } from '../models/PodResponse.js';
import type { PodUpdateRequest } from '../models/PodUpdateRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodsService {
    /**
     * Create Pod
     * Create a new pod
     * @param requestBody
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    public static podCreate(
        requestBody: PodCreateRequest,
    ): CancelablePromise<PodResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List PodS by Organization
     * List all pods in an organization
     * @param organizationId
     * @param limit
     * @param pageToken
     * @returns PodListResponse Successful Response
     * @throws ApiError
     */
    public static podList(
        organizationId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<PodListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/organization/{organization_id}',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Pod
     * Delete a pod
     * @param podId
     * @returns void
     * @throws ApiError
     */
    public static podDelete(
        podId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Pod
     * Get pod details
     * @param podId
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    public static podGet(
        podId: string,
    ): CancelablePromise<PodResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Pod
     * Update pod details
     * @param podId
     * @param requestBody
     * @returns PodResponse Successful Response
     * @throws ApiError
     */
    public static podUpdate(
        podId: string,
        requestBody: PodUpdateRequest,
    ): CancelablePromise<PodResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}',
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
     * Join Pod
     * Self-join a pod when its join policy (ORG_MEMBERS / PUBLIC) allows it
     * @param podId
     * @returns PodMemberResponse Successful Response
     * @throws ApiError
     */
    public static podJoin(
        podId: string,
    ): CancelablePromise<PodMemberResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/join',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
