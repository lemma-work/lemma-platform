/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionRunSummaryResponse } from './FunctionRunSummaryResponse.js';
/**
 * List of function runs.
 */
export type FunctionRunListResponse = {
    items: Array<FunctionRunSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
};
