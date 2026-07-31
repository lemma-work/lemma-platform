/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRunStatus } from './FunctionRunStatus.js';
/**
 * Function run summary for list responses.
 */
export type FunctionRunSummaryResponse = {
    completed_at: (string | null);
    created_at: (string | null);
    function_id: string;
    id: string;
    started_at: (string | null);
    status: FunctionRunStatus;
    user_id: string;
};
