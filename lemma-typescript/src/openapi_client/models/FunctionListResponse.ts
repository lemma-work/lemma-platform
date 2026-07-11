/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionSummaryResponse } from './FunctionSummaryResponse.js';
/**
 * List of functions.
 */
export type FunctionListResponse = {
    items: Array<FunctionSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
};
