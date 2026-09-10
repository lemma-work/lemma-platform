/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MyUsageLimitsResponse } from '../models/MyUsageLimitsResponse.js';
import type { UsageLimitsResponse } from '../models/UsageLimitsResponse.js';
import type { UsageListResponse } from '../models/UsageListResponse.js';
import type { UsageStatsResponse } from '../models/UsageStatsResponse.js';
import type { UsageSummaryResponse } from '../models/UsageSummaryResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class UsageService {
    /**
     * My Events
     * @param organizationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param agentRunId
     * @param conversationId
     * @returns UsageListResponse Successful Response
     * @throws ApiError
     */
    public static usageMeEventsList(
        organizationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 50,
        agentRunId?: (string | null),
        conversationId?: (string | null),
    ): CancelablePromise<UsageListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/me/events',
            query: {
                'organization_id': organizationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * My Limits
     * @param organizationId
     * @returns MyUsageLimitsResponse Successful Response
     * @throws ApiError
     */
    public static usageMeLimitsGet(
        organizationId?: (string | null),
    ): CancelablePromise<MyUsageLimitsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/me/limits',
            query: {
                'organization_id': organizationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * My Stats
     * @param organizationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param agentRunId
     * @param conversationId
     * @returns UsageStatsResponse Successful Response
     * @throws ApiError
     */
    public static usageMeStatsGet(
        organizationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 50,
        agentRunId?: (string | null),
        conversationId?: (string | null),
    ): CancelablePromise<UsageStatsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/me/stats',
            query: {
                'organization_id': organizationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * My Summary
     * @param organizationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param agentRunId
     * @param conversationId
     * @returns UsageSummaryResponse Successful Response
     * @throws ApiError
     */
    public static usageMeSummaryGet(
        organizationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 50,
        agentRunId?: (string | null),
        conversationId?: (string | null),
    ): CancelablePromise<UsageSummaryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/me/summary',
            query: {
                'organization_id': organizationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Usage Events
     * @param organizationId
     * @param agentRunId
     * @param conversationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param podId
     * @param userId
     * @param agentId
     * @param profileId
     * @param profileScope
     * @param modelName
     * @param usageKind
     * @param sourceType
     * @param status
     * @returns UsageListResponse Successful Response
     * @throws ApiError
     */
    public static usageOrganizationEventsList(
        organizationId: string,
        agentRunId?: (string | null),
        conversationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 100,
        podId?: (string | null),
        userId?: (string | null),
        agentId?: (string | null),
        profileId?: (string | null),
        profileScope?: (string | null),
        modelName?: (string | null),
        usageKind?: (string | null),
        sourceType?: (string | null),
        status?: (string | null),
    ): CancelablePromise<UsageListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/organizations/{organization_id}/events',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'pod_id': podId,
                'user_id': userId,
                'agent_id': agentId,
                'profile_id': profileId,
                'profile_scope': profileScope,
                'model_name': modelName,
                'usage_kind': usageKind,
                'source_type': sourceType,
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Usage Limits
     * @param organizationId
     * @returns UsageLimitsResponse Successful Response
     * @throws ApiError
     */
    public static usageOrganizationLimitsGet(
        organizationId: string,
    ): CancelablePromise<UsageLimitsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/organizations/{organization_id}/limits',
            path: {
                'organization_id': organizationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get My Usage
     * @param organizationId
     * @param agentRunId
     * @param conversationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param podId
     * @param userId
     * @param agentId
     * @param profileId
     * @param profileScope
     * @param modelName
     * @param usageKind
     * @param sourceType
     * @param status
     * @returns UsageSummaryResponse Successful Response
     * @throws ApiError
     */
    public static usageOrganizationMeSummaryGet(
        organizationId: string,
        agentRunId?: (string | null),
        conversationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 100,
        podId?: (string | null),
        userId?: (string | null),
        agentId?: (string | null),
        profileId?: (string | null),
        profileScope?: (string | null),
        modelName?: (string | null),
        usageKind?: (string | null),
        sourceType?: (string | null),
        status?: (string | null),
    ): CancelablePromise<UsageSummaryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/organizations/{organization_id}/me',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'pod_id': podId,
                'user_id': userId,
                'agent_id': agentId,
                'profile_id': profileId,
                'profile_scope': profileScope,
                'model_name': modelName,
                'usage_kind': usageKind,
                'source_type': sourceType,
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Usage Stats
     * @param organizationId
     * @param agentRunId
     * @param conversationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param podId
     * @param userId
     * @param agentId
     * @param profileId
     * @param profileScope
     * @param modelName
     * @param usageKind
     * @param sourceType
     * @param status
     * @param granularity
     * @param groupBy
     * @returns UsageStatsResponse Successful Response
     * @throws ApiError
     */
    public static usageOrganizationStatsGet(
        organizationId: string,
        agentRunId?: (string | null),
        conversationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 100,
        podId?: (string | null),
        userId?: (string | null),
        agentId?: (string | null),
        profileId?: (string | null),
        profileScope?: (string | null),
        modelName?: (string | null),
        usageKind?: (string | null),
        sourceType?: (string | null),
        status?: (string | null),
        granularity: string = 'day',
        groupBy?: (string | null),
    ): CancelablePromise<UsageStatsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/organizations/{organization_id}/stats',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'pod_id': podId,
                'user_id': userId,
                'agent_id': agentId,
                'profile_id': profileId,
                'profile_scope': profileScope,
                'model_name': modelName,
                'usage_kind': usageKind,
                'source_type': sourceType,
                'status': status,
                'granularity': granularity,
                'group_by': groupBy,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Organization Usage Summary
     * @param organizationId
     * @param agentRunId
     * @param conversationId
     * @param start
     * @param end
     * @param days
     * @param limit
     * @param podId
     * @param userId
     * @param agentId
     * @param profileId
     * @param profileScope
     * @param modelName
     * @param usageKind
     * @param sourceType
     * @param status
     * @returns UsageSummaryResponse Successful Response
     * @throws ApiError
     */
    public static usageOrganizationSummaryGet(
        organizationId: string,
        agentRunId?: (string | null),
        conversationId?: (string | null),
        start?: (string | null),
        end?: (string | null),
        days: number = 30,
        limit: number = 100,
        podId?: (string | null),
        userId?: (string | null),
        agentId?: (string | null),
        profileId?: (string | null),
        profileScope?: (string | null),
        modelName?: (string | null),
        usageKind?: (string | null),
        sourceType?: (string | null),
        status?: (string | null),
    ): CancelablePromise<UsageSummaryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/usage/organizations/{organization_id}/summary',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'agent_run_id': agentRunId,
                'conversation_id': conversationId,
                'start': start,
                'end': end,
                'days': days,
                'limit': limit,
                'pod_id': podId,
                'user_id': userId,
                'agent_id': agentId,
                'profile_id': profileId,
                'profile_scope': profileScope,
                'model_name': modelName,
                'usage_kind': usageKind,
                'source_type': sourceType,
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
