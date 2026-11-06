/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeInput } from '../models/OutcomeInput.js';
import type { OutcomeSetResponse } from '../models/OutcomeSetResponse.js';
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
     * List Pods by Organization
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
            url: '/organizations/{organization_id}/pods',
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
    /**
     * Clear what the pod owns
     * Let the pod's outcome go. Nothing else about the pod changes, and the weeks its measure was read in are kept: setting the same measure again brings back every week no other measure has been read in since.
     * @param podId
     * @returns void
     * @throws ApiError
     */
    public static outcomeClear(
        podId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/outcome',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set what the pod owns
     * Set what the pod owns, replacing what it had: a bar held every week (kind=week) or a finish line by a date (kind=date), with the target and unit it is held to and the measure that counts it. A measure is one read-only SQL query over the pod's tables that answers a single number, read for every member alike; a new or changed one is read once before it is kept, and refused when the caller cannot read every row it counts (403), names a table that is not there (404), or does not answer one number (422).
     * @param podId
     * @param requestBody
     * @returns OutcomeSetResponse Successful Response
     * @throws ApiError
     */
    public static outcomeSet(
        podId: string,
        requestBody: OutcomeInput,
    ): CancelablePromise<OutcomeSetResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/outcome',
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
