/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request schema for scheduling a cron job.
 */
export type ScheduleCronJobRequest = {
    /**
     * Cron expression (e.g., '*5 * * * *')
     */
    cron_expression: string;
    /**
     * Optional payload for the event
     */
    payload?: (Record<string, any> | null);
    /**
     * Replace job if it already exists
     */
    replace_existing?: boolean;
    /**
     * Schedule ID (also used as job_id)
     */
    schedule_id: string;
};

