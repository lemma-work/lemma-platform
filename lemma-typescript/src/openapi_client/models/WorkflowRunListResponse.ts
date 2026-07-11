/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunSummaryResponse } from './WorkflowRunSummaryResponse.js';
export type WorkflowRunListResponse = {
    items: Array<WorkflowRunSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
};
