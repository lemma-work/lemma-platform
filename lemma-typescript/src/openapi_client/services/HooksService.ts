/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateHookRequest } from '../models/CreateHookRequest.js';
import type { HookResponse } from '../models/HookResponse.js';
import type { HooksResponse } from '../models/HooksResponse.js';
import type { IssuedHookResponse } from '../models/IssuedHookResponse.js';
import type { UpdateHookRequest } from '../models/UpdateHookRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class HooksService {
    /**
     * List Hooks
     * The pod's hooks on tables the caller can read.
     * @param podId
     * @returns HooksResponse Successful Response
     * @throws ApiError
     */
    public static hookList(
        podId: string,
    ): CancelablePromise<HooksResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/hooks',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Make A Hook
     * Make an address a service outside the pod -- a form tool, Zapier, Make -- can post JSON to. Each delivery adds a row to the table, as the member making the hook, who must be able to change the table. The response carries the secret and the address; neither is shown again.
     * @param podId
     * @param requestBody
     * @returns IssuedHookResponse Successful Response
     * @throws ApiError
     */
    public static hookCreate(
        podId: string,
        requestBody: CreateHookRequest,
    ): CancelablePromise<IssuedHookResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/hooks',
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
     * Delete A Hook
     * @param podId
     * @param name
     * @returns void
     * @throws ApiError
     */
    public static hookDelete(
        podId: string,
        name: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/datastore/hooks/{name}',
            path: {
                'pod_id': podId,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get A Hook
     * @param podId
     * @param name
     * @returns HookResponse Successful Response
     * @throws ApiError
     */
    public static hookGet(
        podId: string,
        name: string,
    ): CancelablePromise<HookResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/datastore/hooks/{name}',
            path: {
                'pod_id': podId,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Change A Hook
     * Switch a hook on or off, or change what a delivery becomes.
     * @param podId
     * @param name
     * @param requestBody
     * @returns HookResponse Successful Response
     * @throws ApiError
     */
    public static hookUpdate(
        podId: string,
        name: string,
        requestBody: UpdateHookRequest,
    ): CancelablePromise<HookResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/datastore/hooks/{name}',
            path: {
                'pod_id': podId,
                'name': name,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Give A Hook A New Secret
     * The old address stops working at once. The response carries the new secret and address; neither is shown again.
     * @param podId
     * @param name
     * @returns IssuedHookResponse Successful Response
     * @throws ApiError
     */
    public static hookRotate(
        podId: string,
        name: string,
    ): CancelablePromise<IssuedHookResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/datastore/hooks/{name}/rotate',
            path: {
                'pod_id': podId,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
