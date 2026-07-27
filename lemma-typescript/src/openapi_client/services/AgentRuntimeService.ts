/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHarnessListResponse } from '../models/AgentHarnessListResponse.js';
import type { AgentRuntimeProfileListResponse } from '../models/AgentRuntimeProfileListResponse.js';
import type { AgentRuntimeProfileResponse } from '../models/AgentRuntimeProfileResponse.js';
import type { CreateAgentHostRuntimeProfileRequest } from '../models/CreateAgentHostRuntimeProfileRequest.js';
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from '../models/CreateAnthropicCompatibleRuntimeProfileRequest.js';
import type { CreateOpenAICompatibleRuntimeProfileRequest } from '../models/CreateOpenAICompatibleRuntimeProfileRequest.js';
import type { CreateUserDaemonRuntimeProfileRequest } from '../models/CreateUserDaemonRuntimeProfileRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentRuntimeService {
    /**
     * List Available Agent Harnesses
     * @returns AgentHarnessListResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeHarnessesList(): CancelablePromise<AgentHarnessListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agent-runtime/harnesses',
        });
    }
    /**
     * List Available Agent Runtime Profiles
     * @param orgId
     * @returns AgentRuntimeProfileListResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesList(
        orgId: string,
    ): CancelablePromise<AgentRuntimeProfileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/agent-runtime/profiles',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent Runtime Profile
     * @param orgId
     * @param requestBody
     * @returns AgentRuntimeProfileResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesCreate(
        orgId: string,
        requestBody: (CreateUserDaemonRuntimeProfileRequest | CreateOpenAICompatibleRuntimeProfileRequest | CreateAnthropicCompatibleRuntimeProfileRequest | CreateAgentHostRuntimeProfileRequest),
    ): CancelablePromise<AgentRuntimeProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{org_id}/agent-runtime/profiles',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
