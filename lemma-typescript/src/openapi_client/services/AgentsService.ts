/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentActionResponse } from '../models/AgentActionResponse.js';
import type { AgentDetailResponse } from '../models/AgentDetailResponse.js';
import type { AgentListResponse } from '../models/AgentListResponse.js';
import type { AgentMessageResponse } from '../models/AgentMessageResponse.js';
import type { AgentPermissionsReplaceRequest } from '../models/AgentPermissionsReplaceRequest.js';
import type { AgentPermissionsResponse } from '../models/AgentPermissionsResponse.js';
import type { CreateAgentRequest } from '../models/CreateAgentRequest.js';
import type { UpdateAgentRequest } from '../models/UpdateAgentRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentsService {
    /**
     * List Agents
     * List pod-owned agent definitions visible to the current user.
     * @param podId
     * @param pageToken
     * @param limit
     * @param include Extra data to embed. `permissions` attaches each agent's resource grants, resolved for the whole page in one query — without it, a caller that needs grants must call the per-agent permissions endpoint once per row.
     * @returns AgentListResponse Successful Response
     * @throws ApiError
     */
    public static agentList(
        podId: string,
        pageToken?: (string | null),
        limit: number = 100,
        include?: Array<string>,
    ): CancelablePromise<AgentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/agents',
            path: {
                'pod_id': podId,
            },
            query: {
                'page_token': pageToken,
                'limit': limit,
                'include': include,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent
     * Create a pod-owned agent definition with runtime, toolsets, and schemas.
     * @param podId
     * @param requestBody
     * @returns AgentActionResponse Successful Response
     * @throws ApiError
     */
    public static agentCreate(
        podId: string,
        requestBody: CreateAgentRequest,
    ): CancelablePromise<AgentActionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/agents',
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
     * Delete Agent
     * Delete a pod-owned agent definition by name.
     * @param podId
     * @param agentName
     * @returns AgentMessageResponse Successful Response
     * @throws ApiError
     */
    public static agentDelete(
        podId: string,
        agentName: string,
    ): CancelablePromise<AgentMessageResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/agents/{agent_name}',
            path: {
                'pod_id': podId,
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent
     * Get one pod-owned agent definition by its stable name.
     * @param podId
     * @param agentName
     * @returns AgentDetailResponse Successful Response
     * @throws ApiError
     */
    public static agentGet(
        podId: string,
        agentName: string,
    ): CancelablePromise<AgentDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/agents/{agent_name}',
            path: {
                'pod_id': podId,
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Agent
     * Update an agent definition, including prompt instruction, runtime, toolsets, and schemas.
     * @param podId
     * @param agentName
     * @param requestBody
     * @returns AgentActionResponse Successful Response
     * @throws ApiError
     */
    public static agentUpdate(
        podId: string,
        agentName: string,
        requestBody: UpdateAgentRequest,
    ): CancelablePromise<AgentActionResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/agents/{agent_name}',
            path: {
                'pod_id': podId,
                'agent_name': agentName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent Resource Permissions
     * Get explicit resource grants assigned to an agent.
     * @param podId
     * @param agentName
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static agentPermissionsGet(
        podId: string,
        agentName: string,
    ): CancelablePromise<AgentPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/agents/{agent_name}/permissions',
            path: {
                'pod_id': podId,
                'agent_name': agentName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Replace Agent Resource Permissions
     * Replace explicit resource grants assigned to an agent.
     * @param podId
     * @param agentName
     * @param requestBody
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static agentPermissionsReplace(
        podId: string,
        agentName: string,
        requestBody: AgentPermissionsReplaceRequest,
    ): CancelablePromise<AgentPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/agents/{agent_name}/permissions',
            path: {
                'pod_id': podId,
                'agent_name': agentName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
