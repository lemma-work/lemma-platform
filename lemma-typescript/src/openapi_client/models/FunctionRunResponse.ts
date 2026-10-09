/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRunStatus } from './FunctionRunStatus.js';
import type { JsonObject } from './JsonObject.js';
/**
 * Function run response.
 */
export type FunctionRunResponse = {
    /**
     * Who the run acted for: `user:{id}` for a member, `contact:{id}` for a contact's call (which runs as the function itself, with no member), or `anonymous`.
     */
    actor?: (string | null);
    completed_at: (string | null);
    contact_id?: (string | null);
    created_at: (string | null);
    error?: (string | null);
    function_id: string;
    id: string;
    input_data?: (JsonObject | null);
    job_id?: (string | null);
    logs?: (string | null);
    output_data?: (JsonObject | null);
    revision_hash?: (string | null);
    started_at: (string | null);
    status: FunctionRunStatus;
    user_email?: (string | null);
    user_id?: (string | null);
};
