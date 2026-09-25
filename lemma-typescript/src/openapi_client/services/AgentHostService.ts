/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostHarnessListResponse } from '../models/AgentHostHarnessListResponse.js';
import type { AgentHostListResponse } from '../models/AgentHostListResponse.js';
import type { AgentHostPairingCreate } from '../models/AgentHostPairingCreate.js';
import type { AgentHostPairingCreated } from '../models/AgentHostPairingCreated.js';
import type { AgentHostResponse } from '../models/AgentHostResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentHostService {
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
     *
     * The secret stops authenticating the moment this commits, but a link opened
     * with it before then is already past authentication. The notice closes it,
     * on whichever replica holds it, instead of leaving it working until the host
     * happens to reconnect.
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
