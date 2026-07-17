/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response schema for job status.
 */
export type JobStatusResponse = {
    exists: boolean;
    job_id: string;
    job_state?: (string | null);
    next_run_time?: (string | null);
};

