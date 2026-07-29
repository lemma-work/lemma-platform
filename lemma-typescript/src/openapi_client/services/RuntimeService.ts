/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeProfileListResponse } from '../models/AgentRuntimeProfileListResponse.js';
import type { AnthropicCompatibleRuntimeProfileResponse } from '../models/AnthropicCompatibleRuntimeProfileResponse.js';
import type { AzureOpenAIRuntimeProfileResponse } from '../models/AzureOpenAIRuntimeProfileResponse.js';
import type { CreateAnthropicCompatibleRuntimeProfileRequest } from '../models/CreateAnthropicCompatibleRuntimeProfileRequest.js';
import type { CreateAzureOpenAIRuntimeProfileRequest } from '../models/CreateAzureOpenAIRuntimeProfileRequest.js';
import type { CreateGoogleVertexRuntimeProfileRequest } from '../models/CreateGoogleVertexRuntimeProfileRequest.js';
import type { CreateHarnessRuntimeProfileRequest } from '../models/CreateHarnessRuntimeProfileRequest.js';
import type { CreateOpenAICompatibleRuntimeProfileRequest } from '../models/CreateOpenAICompatibleRuntimeProfileRequest.js';
import type { GoogleVertexRuntimeProfileResponse } from '../models/GoogleVertexRuntimeProfileResponse.js';
import type { HarnessRuntimeProfileResponse } from '../models/HarnessRuntimeProfileResponse.js';
import type { OpenAICompatibleRuntimeProfileResponse } from '../models/OpenAICompatibleRuntimeProfileResponse.js';
import type { UpdateRuntimeProfileRequest } from '../models/UpdateRuntimeProfileRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class RuntimeService {
    /**
     * List runtime profiles
     * @param orgId
     * @returns AgentRuntimeProfileListResponse Successful Response
     * @throws ApiError
     */
    public static runtimeProfilesList(
        orgId: string,
    ): CancelablePromise<AgentRuntimeProfileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/runtime/profiles',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create a runtime profile
     * @param orgId
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static runtimeProfilesCreate(
        orgId: string,
        requestBody: (CreateOpenAICompatibleRuntimeProfileRequest | CreateAnthropicCompatibleRuntimeProfileRequest | CreateAzureOpenAIRuntimeProfileRequest | CreateGoogleVertexRuntimeProfileRequest | CreateHarnessRuntimeProfileRequest),
    ): CancelablePromise<(OpenAICompatibleRuntimeProfileResponse | AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse)> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{org_id}/runtime/profiles',
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
    /**
     * Disable a runtime profile
     * @param orgId
     * @param profileId
     * @returns void
     * @throws ApiError
     */
    public static runtimeProfilesDelete(
        orgId: string,
        profileId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/{org_id}/runtime/profiles/{profile_id}',
            path: {
                'org_id': orgId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get a runtime profile
     * @param orgId
     * @param profileId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static runtimeProfilesGet(
        orgId: string,
        profileId: string,
    ): CancelablePromise<(OpenAICompatibleRuntimeProfileResponse | AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{org_id}/runtime/profiles/{profile_id}',
            path: {
                'org_id': orgId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update a runtime profile
     * @param orgId
     * @param profileId
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static runtimeProfilesUpdate(
        orgId: string,
        profileId: string,
        requestBody: UpdateRuntimeProfileRequest,
    ): CancelablePromise<(OpenAICompatibleRuntimeProfileResponse | AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse)> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/organizations/{org_id}/runtime/profiles/{profile_id}',
            path: {
                'org_id': orgId,
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
     * Refresh a runtime profile
     * @param orgId
     * @param profileId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static runtimeProfilesRefresh(
        orgId: string,
        profileId: string,
    ): CancelablePromise<(OpenAICompatibleRuntimeProfileResponse | AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse)> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{org_id}/runtime/profiles/{profile_id}/refresh',
            path: {
                'org_id': orgId,
                'profile_id': profileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
