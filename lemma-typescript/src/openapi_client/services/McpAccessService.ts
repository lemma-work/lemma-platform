/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectedClientsResponse } from '../models/ConnectedClientsResponse.js';
import type { McpEndpointResponse } from '../models/McpEndpointResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class McpAccessService {
    /**
     * MCP clients you have connected
     * @param podId Only this pod's.
     * @param everyone Every member's connections to pod_id. Pod admins only.
     * @returns ConnectedClientsResponse Successful Response
     * @throws ApiError
     */
    public static mcpAccessGrantsList(
        podId?: (string | null),
        everyone: boolean = false,
    ): CancelablePromise<ConnectedClientsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/grants',
            query: {
                'pod_id': podId,
                'everyone': everyone,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Disconnect an MCP client
     * Ends the grant and every token it issued. The client's next request is
     * refused and it has to ask the person again. A pod's admins may end any
     * member's connection to their pod.
     * @param grantId
     * @returns void
     * @throws ApiError
     */
    public static mcpAccessGrantsRevoke(
        grantId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/oauth/grants/{grant_id}',
            path: {
                'grant_id': grantId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * The MCP URL for a pod
     * Built here rather than in the browser, which knows the API's address
     * only as the page was configured -- not necessarily as clients reach it.
     * @param podId
     * @returns McpEndpointResponse Successful Response
     * @throws ApiError
     */
    public static mcpAccessEndpointGet(
        podId: string,
    ): CancelablePromise<McpEndpointResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth/mcp-endpoint/{pod_id}',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
