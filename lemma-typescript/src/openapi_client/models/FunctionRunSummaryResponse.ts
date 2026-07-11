/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRunStatus } from './FunctionRunStatus.js';
/**
 * Function run summary for list responses.
 */
export type FunctionRunSummaryResponse = {
    completed_at: any;
    created_at: any;
    function_id: string;
    id: string;
    started_at: any;
    status: FunctionRunStatus;
    user_id: string;
};
