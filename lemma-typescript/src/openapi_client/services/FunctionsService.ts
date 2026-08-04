/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateFunctionRequest } from '../models/CreateFunctionRequest.js';
import type { ExecuteFunctionRequest } from '../models/ExecuteFunctionRequest.js';
import type { FunctionActionResponse } from '../models/FunctionActionResponse.js';
import type { FunctionDetailResponse } from '../models/FunctionDetailResponse.js';
import type { FunctionListResponse } from '../models/FunctionListResponse.js';
import type { FunctionMessageResponse } from '../models/FunctionMessageResponse.js';
import type { FunctionPermissionsReplaceRequest } from '../models/FunctionPermissionsReplaceRequest.js';
import type { FunctionPermissionsResponse } from '../models/FunctionPermissionsResponse.js';
import type { FunctionRunListResponse } from '../models/FunctionRunListResponse.js';
import type { FunctionRunResponse } from '../models/FunctionRunResponse.js';
import type { UpdateFunctionRequest } from '../models/UpdateFunctionRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class FunctionsService {
    /**
     * List Functions
     * List all functions in a pod
     * @param podId
     * @param limit
     * @param pageToken
     * @param include Extra data to embed. `permissions` attaches each function's resource grants, resolved for the whole page in one query — without it, a caller that needs grants must call the per-function permissions endpoint once per row.
     * @returns FunctionListResponse Successful Response
     * @throws ApiError
     */
    public static functionList(
        podId: string,
        limit: number = 100,
        pageToken?: (string | null),
        include?: Array<string>,
    ): CancelablePromise<FunctionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/functions',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
                'include': include,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Function
     * Create a new function in a pod. Do not send input_schema, output_schema, or config_schema; the platform derives those schemas from the function code and returns them in the response.
     * @param podId
     * @param requestBody
     * @returns FunctionActionResponse Successful Response
     * @throws ApiError
     */
    public static functionCreate(
        podId: string,
        requestBody: CreateFunctionRequest,
    ): CancelablePromise<FunctionActionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/functions',
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
     * Delete Function
     * Delete a function
     * @param podId
     * @param functionName
     * @returns FunctionMessageResponse Successful Response
     * @throws ApiError
     */
    public static functionDelete(
        podId: string,
        functionName: string,
    ): CancelablePromise<FunctionMessageResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/functions/{function_name}',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Function
     * Get a function by name
     * @param podId
     * @param functionName
     * @returns FunctionDetailResponse Successful Response
     * @throws ApiError
     */
    public static functionGet(
        podId: string,
        functionName: string,
    ): CancelablePromise<FunctionDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/functions/{function_name}',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Function
     * Update a function. When code is supplied, the platform re-derives the function input_schema and output_schema and returns the refreshed function.
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionActionResponse Successful Response
     * @throws ApiError
     */
    public static functionUpdate(
        podId: string,
        functionName: string,
        requestBody: UpdateFunctionRequest,
    ): CancelablePromise<FunctionActionResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/functions/{function_name}',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Function Resource Permissions
     * Get explicit resource grants assigned to a function.
     * @param podId
     * @param functionName
     * @returns FunctionPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static functionPermissionsGet(
        podId: string,
        functionName: string,
    ): CancelablePromise<FunctionPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/functions/{function_name}/permissions',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Replace Function Resource Permissions
     * Replace explicit resource grants assigned to a function.
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static functionPermissionsReplace(
        podId: string,
        functionName: string,
        requestBody: FunctionPermissionsReplaceRequest,
    ): CancelablePromise<FunctionPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/functions/{function_name}/permissions',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Runs
     * List runs for a function
     * @param podId
     * @param functionName
     * @param limit
     * @param pageToken
     * @returns FunctionRunListResponse Successful Response
     * @throws ApiError
     */
    public static functionRunList(
        podId: string,
        functionName: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<FunctionRunListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/functions/{function_name}/runs',
            path: {
                'pod_id': podId,
                'function_name': functionName,
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
     * Execute Function
     * Execute a function
     * @param podId
     * @param functionName
     * @param requestBody
     * @returns FunctionRunResponse Successful Response
     * @throws ApiError
     */
    public static functionRun(
        podId: string,
        functionName: string,
        requestBody: ExecuteFunctionRequest,
    ): CancelablePromise<FunctionRunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/functions/{function_name}/runs',
            path: {
                'pod_id': podId,
                'function_name': functionName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Run
     * Get a specific function run
     * @param podId
     * @param functionName
     * @param runId
     * @returns FunctionRunResponse Successful Response
     * @throws ApiError
     */
    public static functionRunGet(
        podId: string,
        functionName: string,
        runId: string,
    ): CancelablePromise<FunctionRunResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/functions/{function_name}/runs/{run_id}',
            path: {
                'pod_id': podId,
                'function_name': functionName,
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
