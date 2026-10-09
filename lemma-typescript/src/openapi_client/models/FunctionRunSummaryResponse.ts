/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRunStatus } from './FunctionRunStatus.js';
/**
 * Function run summary for list responses.
 */
export type FunctionRunSummaryResponse = {
    /**
     * Who the run acted for: `user:{id}` for a member, `contact:{id}` for a contact's call (which runs as the function itself, with no member), or `anonymous`.
     */
    actor?: (string | null);
    completed_at: (string | null);
    contact_id?: (string | null);
    created_at: (string | null);
    function_id: string;
    id: string;
    started_at: (string | null);
    status: FunctionRunStatus;
    user_id?: (string | null);
};
