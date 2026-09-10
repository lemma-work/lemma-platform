/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeProfileDetailResponse } from '../models/AgentRuntimeProfileDetailResponse.js';
import type { AgentRuntimeProfileListResponse } from '../models/AgentRuntimeProfileListResponse.js';
import type { AgentRuntimeProfileResponse } from '../models/AgentRuntimeProfileResponse.js';
import type { CreateAgentHostRuntimeProfileRequest } from '../models/CreateAgentHostRuntimeProfileRequest.js';
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from '../models/CreateAnthropicCompatibleRuntimeProfileRequest.js';
import type { CreateOpenAICompatibleRuntimeProfileRequest } from '../models/CreateOpenAICompatibleRuntimeProfileRequest.js';
import type { UpdateAgentHostRuntimeProfileRequest } from '../models/UpdateAgentHostRuntimeProfileRequest.js';
import type { UpdateAnthropicCompatibleRuntimeProfileRequest } from '../models/UpdateAnthropicCompatibleRuntimeProfileRequest.js';
import type { UpdateOpenAICompatibleRuntimeProfileRequest } from '../models/UpdateOpenAICompatibleRuntimeProfileRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentRuntimeService {
    /**
     * List Available Agent Runtime Profiles
     * @param organizationId
     * @param includeDisabled
     * @returns AgentRuntimeProfileListResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesList(
        organizationId: string,
        includeDisabled: boolean = false,
    ): CancelablePromise<AgentRuntimeProfileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/agent-runtime/profiles',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'include_disabled': includeDisabled,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Agent Runtime Profile
     * @param organizationId
     * @param requestBody
     * @returns AgentRuntimeProfileResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesCreate(
        organizationId: string,
        requestBody: (CreateAgentHostRuntimeProfileRequest | CreateOpenAICompatibleRuntimeProfileRequest | CreateAnthropicCompatibleRuntimeProfileRequest),
    ): CancelablePromise<AgentRuntimeProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/agent-runtime/profiles',
            path: {
                'organization_id': organizationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Archive Agent Runtime Profile
     * @param organizationId
     * @param profileId
     * @returns void
     * @throws ApiError
     */
    public static agentRuntimeProfilesArchive(
        organizationId: string,
        profileId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/{organization_id}/agent-runtime/profiles/{profile_id}',
            path: {
                'organization_id': organizationId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent Runtime Profile
     * @param organizationId
     * @param profileId
     * @returns AgentRuntimeProfileDetailResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesGet(
        organizationId: string,
        profileId: string,
    ): CancelablePromise<AgentRuntimeProfileDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/agent-runtime/profiles/{profile_id}',
            path: {
                'organization_id': organizationId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Agent Runtime Profile
     * @param organizationId
     * @param profileId
     * @param requestBody
     * @returns AgentRuntimeProfileResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesUpdate(
        organizationId: string,
        profileId: string,
        requestBody: (UpdateAgentHostRuntimeProfileRequest | UpdateOpenAICompatibleRuntimeProfileRequest | UpdateAnthropicCompatibleRuntimeProfileRequest),
    ): CancelablePromise<AgentRuntimeProfileResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/organizations/{organization_id}/agent-runtime/profiles/{profile_id}',
            path: {
                'organization_id': organizationId,
                'profile_id': profileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Restore Agent Runtime Profile
     * @param organizationId
     * @param profileId
     * @returns AgentRuntimeProfileResponse Successful Response
     * @throws ApiError
     */
    public static agentRuntimeProfilesRestore(
        organizationId: string,
        profileId: string,
    ): CancelablePromise<AgentRuntimeProfileResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/agent-runtime/profiles/{profile_id}/restore',
            path: {
                'organization_id': organizationId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
