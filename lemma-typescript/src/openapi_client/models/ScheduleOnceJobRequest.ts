/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request schema for scheduling a one-time job.
 */
export type ScheduleOnceJobRequest = {
    /**
     * Optional payload for the event
     */
    payload?: (Record<string, any> | null);
    /**
     * Replace job if it already exists
     */
    replace_existing?: boolean;
    /**
     * When to run the job (ISO datetime string)
     */
    run_date: string;
    /**
     * Schedule ID (also used as job_id)
     */
    schedule_id: string;
};

