/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TableSummaryResponse } from './TableSummaryResponse.js';
/**
 * Schema for table list response.
 */
export type TableListResponse = {
    items: Array<TableSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
    total?: (number | null);
};
