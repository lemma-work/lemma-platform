/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowSummaryResponse } from './WorkflowSummaryResponse.js';
export type WorkflowListResponse = {
    items: Array<WorkflowSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
};
