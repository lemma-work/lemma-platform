/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostEventAck } from '../models/AgentHostEventAck.js';
import type { AgentHostEventBatch } from '../models/AgentHostEventBatch.js';
import type { AgentHostHarnessListResponse } from '../models/AgentHostHarnessListResponse.js';
import type { AgentHostHarnessPublishRequest } from '../models/AgentHostHarnessPublishRequest.js';
import type { AgentHostHarnessPublishResponse } from '../models/AgentHostHarnessPublishResponse.js';
import type { AgentHostListResponse } from '../models/AgentHostListResponse.js';
import type { AgentHostMcpRouteResponse } from '../models/AgentHostMcpRouteResponse.js';
import type { AgentHostPairingComplete } from '../models/AgentHostPairingComplete.js';
import type { AgentHostPairingCompleted } from '../models/AgentHostPairingCompleted.js';
import type { AgentHostPairingCreate } from '../models/AgentHostPairingCreate.js';
import type { AgentHostPairingCreated } from '../models/AgentHostPairingCreated.js';
import type { AgentHostPollRequest } from '../models/AgentHostPollRequest.js';
import type { AgentHostPollResponse } from '../models/AgentHostPollResponse.js';
import type { AgentHostResponse } from '../models/AgentHostResponse.js';
import type { AgentHostTokenExchange } from '../models/AgentHostTokenExchange.js';
import type { AgentHostTokenResponse } from '../models/AgentHostTokenResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentHostService {
    /**
     * Append Agent Host Events
     * @param requestBody
     * @param authorization
     * @returns AgentHostEventAck Successful Response
     * @throws ApiError
     */
    public static agentHostEventsAppend(
        requestBody: AgentHostEventBatch,
        authorization?: (string | null),
    ): CancelablePromise<AgentHostEventAck> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agent-host/events:append',
            headers: {
                'authorization': authorization,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Publish Agent Host Harnesses
     * @param requestBody
     * @param authorization
     * @returns AgentHostHarnessPublishResponse Successful Response
     * @throws ApiError
     */
    public static agentHostHarnessesPublish(
        requestBody: AgentHostHarnessPublishRequest,
        authorization?: (string | null),
    ): CancelablePromise<AgentHostHarnessPublishResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agent-host/harnesses',
            headers: {
                'authorization': authorization,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Resolve Agent Host Mcp Route
     * @param routeId
     * @param authorization
     * @returns AgentHostMcpRouteResponse Successful Response
     * @throws ApiError
     */
    public static agentHostMcpRouteResolve(
        routeId: string,
        authorization?: (string | null),
    ): CancelablePromise<AgentHostMcpRouteResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agent-host/mcp-routes/{route_id}',
            path: {
                'route_id': routeId,
            },
            headers: {
                'authorization': authorization,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Complete Agent Host Pairing
     * @param requestBody
     * @returns AgentHostPairingCompleted Successful Response
     * @throws ApiError
     */
    public static agentHostPairingComplete(
        requestBody: AgentHostPairingComplete,
    ): CancelablePromise<AgentHostPairingCompleted> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agent-host/pairings:complete',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Poll Agent Host Commands
     * @param requestBody
     * @param authorization
     * @returns AgentHostPollResponse Successful Response
     * @throws ApiError
     */
    public static agentHostPoll(
        requestBody: AgentHostPollRequest,
        authorization?: (string | null),
    ): CancelablePromise<AgentHostPollResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agent-host/poll',
            headers: {
                'authorization': authorization,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Self Revoke Agent Host
     * Revoke the calling device before its local identity is removed.
     * @param authorization
     * @returns void
     * @throws ApiError
     */
    public static agentHostSelfRevoke(
        authorization?: (string | null),
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agent-host/revoke',
            headers: {
                'authorization': authorization,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Exchange Agent Host Token
     * @param requestBody
     * @returns AgentHostTokenResponse Successful Response
     * @throws ApiError
     */
    public static agentHostTokenExchange(
        requestBody: AgentHostTokenExchange,
    ): CancelablePromise<AgentHostTokenResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agent-host/token:exchange',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent Host Pairing
     * @param requestBody
     * @returns AgentHostPairingCreated Successful Response
     * @throws ApiError
     */
    public static agentHostPairingCreate(
        requestBody: AgentHostPairingCreate,
    ): CancelablePromise<AgentHostPairingCreated> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/me/runtime/agent-host-pairings',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Agent Hosts
     * @returns AgentHostListResponse Successful Response
     * @throws ApiError
     */
    public static agentHostList(): CancelablePromise<AgentHostListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/me/runtime/agent-hosts',
        });
    }
    /**
     * Revoke Agent Host
     * @param hostId
     * @returns AgentHostResponse Successful Response
     * @throws ApiError
     */
    public static agentHostRevoke(
        hostId: string,
    ): CancelablePromise<AgentHostResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/me/runtime/agent-hosts/{host_id}',
            path: {
                'host_id': hostId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Agent Host Harnesses
     * @param hostId
     * @returns AgentHostHarnessListResponse Successful Response
     * @throws ApiError
     */
    public static agentHostHarnessesList(
        hostId: string,
    ): CancelablePromise<AgentHostHarnessListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/me/runtime/agent-hosts/{host_id}/harnesses',
            path: {
                'host_id': hostId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
