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
import type { AgentHostPairingComplete } from '../models/AgentHostPairingComplete.js';
import type { AgentHostPairingCompleted } from '../models/AgentHostPairingCompleted.js';
import type { AgentHostPairingCreate } from '../models/AgentHostPairingCreate.js';
import type { AgentHostPairingCreated } from '../models/AgentHostPairingCreated.js';
import type { AgentHostPollRequest } from '../models/AgentHostPollRequest.js';
import type { AgentHostPollResponse } from '../models/AgentHostPollResponse.js';
import type { AgentHostResponse } from '../models/AgentHostResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentHostService {
    /**
     * Append Agent Host Events
     * Append one ordered batch to the run's stream.
     *
     * There is no second lane to publish on: every event type travels the one
     * ordered stream, and the ack watermark is the stream's last entry.
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
     * Replace this host's harness snapshots with the reported set.
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
     * Complete Agent Host Pairing
     * Consume a pairing code and issue the host secret, shown exactly once.
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
     * Long-poll for commands, carrying the host's control updates up.
     *
     * This owns its own units of work rather than the request-scoped one: the
     * idle wait below can hold the connection open for 25 seconds, and a
     * transaction must not stay open across it.
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
     * Let a host retire its own credential, e.g. on uninstall.
     * @param authorization
     * @returns AgentHostResponse Successful Response
     * @throws ApiError
     */
    public static agentHostSelfRevoke(
        authorization?: (string | null),
    ): CancelablePromise<AgentHostResponse> {
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
     * Create Agent Host Pairing
     * Mint a short-lived pairing code for a machine this user controls.
     *
     * A paired computer is the user's, not a workspace's: nothing here needs an
     * organization. Sharing it happens later, by giving a runtime profile
     * ORGANIZATION scope.
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
     * Revoke a host, invalidating its secret immediately.
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
