/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallConfigResponse } from '../models/CallConfigResponse.js';
import type { CallResponse } from '../models/CallResponse.js';
import type { OpenCallRequest } from '../models/OpenCallRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class CallsService {
    /**
     * Open a call
     * Open a live voice call with a pod agent. The call is a conversation: its agent coordinates the call, hands work to sub-agents and shows things on the call's canvas. Connect to `/pods/{pod_id}/calls/{call_id}/socket` to talk.
     * @param podId
     * @param requestBody
     * @returns CallResponse Successful Response
     * @throws ApiError
     */
    public static callOpen(
        podId: string,
        requestBody: OpenCallRequest,
    ): CancelablePromise<CallResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/calls',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                403: `The caller may not run this agent.`,
                404: `The agent was not found.`,
                422: `Validation Error`,
                503: `No voice engine is configured.`,
            },
        });
    }
    /**
     * Call configuration
     * Which voice engines this server can open a call with.
     * @param podId
     * @returns CallConfigResponse Successful Response
     * @throws ApiError
     */
    public static callConfig(
        podId: string,
    ): CancelablePromise<CallConfigResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/calls/config',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
