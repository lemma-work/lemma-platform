/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JobListResponse } from '../models/JobListResponse.js';
import type { JobResponse } from '../models/JobResponse.js';
import type { JobStatusResponse } from '../models/JobStatusResponse.js';
import type { ScheduleCronJobRequest } from '../models/ScheduleCronJobRequest.js';
import type { ScheduleOnceJobRequest } from '../models/ScheduleOnceJobRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class SchedulerService {
    /**
     * List All Jobs
     * Get a list of all scheduled jobs
     * @param authorization
     * @returns JobListResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobList(
        authorization?: (string | null),
    ): CancelablePromise<JobListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/scheduler/jobs',
            headers: {
                'authorization': authorization,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Schedule Cron Job
     * Schedule a recurring job using a cron expression
     * @param requestBody
     * @param authorization
     * @returns JobResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobScheduleCron(
        requestBody: ScheduleCronJobRequest,
        authorization?: (string | null),
    ): CancelablePromise<JobResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/scheduler/jobs/cron',
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
     * Schedule One-Time Job
     * Schedule a job to run once at a specific time
     * @param requestBody
     * @param authorization
     * @returns JobResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobScheduleOnce(
        requestBody: ScheduleOnceJobRequest,
        authorization?: (string | null),
    ): CancelablePromise<JobResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/scheduler/jobs/once',
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
     * Remove Job
     * Remove a scheduled job by schedule_id
     * @param scheduleId
     * @param authorization
     * @returns void
     * @throws ApiError
     */
    public static schedulerJobDelete(
        scheduleId: string,
        authorization?: (string | null),
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/scheduler/jobs/{schedule_id}',
            path: {
                'schedule_id': scheduleId,
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
     * Get Job Status
     * Get the status of a specific job by schedule_id
     * @param scheduleId
     * @param authorization
     * @returns JobStatusResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobGet(
        scheduleId: string,
        authorization?: (string | null),
    ): CancelablePromise<JobStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/scheduler/jobs/{schedule_id}',
            path: {
                'schedule_id': scheduleId,
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
     * Pause Job
     * Pause a scheduled job by schedule_id
     * @param scheduleId
     * @param authorization
     * @returns JobStatusResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobPause(
        scheduleId: string,
        authorization?: (string | null),
    ): CancelablePromise<JobStatusResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/scheduler/jobs/{schedule_id}/pause',
            path: {
                'schedule_id': scheduleId,
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
     * Resume Job
     * Resume a paused job by schedule_id
     * @param scheduleId
     * @param authorization
     * @returns JobStatusResponse Successful Response
     * @throws ApiError
     */
    public static schedulerJobResume(
        scheduleId: string,
        authorization?: (string | null),
    ): CancelablePromise<JobStatusResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/scheduler/jobs/{schedule_id}/resume',
            path: {
                'schedule_id': scheduleId,
            },
            headers: {
                'authorization': authorization,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
