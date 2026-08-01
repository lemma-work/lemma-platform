/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreateScheduleRequest } from '../models/CreateScheduleRequest.js';
import type { ScheduleDetailResponse } from '../models/ScheduleDetailResponse.js';
import type { ScheduleListResponse } from '../models/ScheduleListResponse.js';
import type { ScheduleRunListResponse } from '../models/ScheduleRunListResponse.js';
import type { ScheduleRunResponse } from '../models/ScheduleRunResponse.js';
import type { ScheduleType } from '../models/ScheduleType.js';
import type { UpdateScheduleRequest } from '../models/UpdateScheduleRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class SchedulesService {
    /**
     * List Schedules
     * List pod schedules.
     * @param podId
     * @param scheduleType
     * @param isActive
     * @param agentName
     * @param workflowName
     * @param name
     * @param limit
     * @param pageToken
     * @returns ScheduleListResponse Successful Response
     * @throws ApiError
     */
    public static scheduleList(
        podId: string,
        scheduleType?: (ScheduleType | null),
        isActive?: (boolean | null),
        agentName?: (string | null),
        workflowName?: (string | null),
        name?: (string | null),
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<ScheduleListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/schedules',
            path: {
                'pod_id': podId,
            },
            query: {
                'schedule_type': scheduleType,
                'is_active': isActive,
                'agent_name': agentName,
                'workflow_name': workflowName,
                'name': name,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Schedule
     * Create a new pod schedule.
     * @param podId
     * @param requestBody
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    public static scheduleCreate(
        podId: string,
        requestBody: CreateScheduleRequest,
    ): CancelablePromise<ScheduleDetailResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/schedules',
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
     * Delete Schedule
     * Delete a schedule.
     * @param podId
     * @param scheduleId
     * @returns void
     * @throws ApiError
     */
    public static scheduleDelete(
        podId: string,
        scheduleId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/schedules/{schedule_id}',
            path: {
                'pod_id': podId,
                'schedule_id': scheduleId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Schedule
     * Get a schedule by ID.
     * @param podId
     * @param scheduleId
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    public static scheduleGet(
        podId: string,
        scheduleId: string,
    ): CancelablePromise<ScheduleDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/schedules/{schedule_id}',
            path: {
                'pod_id': podId,
                'schedule_id': scheduleId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Schedule
     * Update a schedule.
     * @param podId
     * @param scheduleId
     * @param requestBody
     * @returns ScheduleDetailResponse Successful Response
     * @throws ApiError
     */
    public static scheduleUpdate(
        podId: string,
        scheduleId: string,
        requestBody: UpdateScheduleRequest,
    ): CancelablePromise<ScheduleDetailResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/schedules/{schedule_id}',
            path: {
                'pod_id': podId,
                'schedule_id': scheduleId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Schedule Runs
     * @param podId
     * @param scheduleId
     * @param limit
     * @returns ScheduleRunListResponse Successful Response
     * @throws ApiError
     */
    public static scheduleRunList(
        podId: string,
        scheduleId: string,
        limit: number = 100,
    ): CancelablePromise<ScheduleRunListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/schedules/{schedule_id}/runs',
            path: {
                'pod_id': podId,
                'schedule_id': scheduleId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retry Schedule Run
     * @param podId
     * @param scheduleId
     * @param runId
     * @returns ScheduleRunResponse Successful Response
     * @throws ApiError
     */
    public static scheduleRunRetry(
        podId: string,
        scheduleId: string,
        runId: string,
    ): CancelablePromise<ScheduleRunResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry',
            path: {
                'pod_id': podId,
                'schedule_id': scheduleId,
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
