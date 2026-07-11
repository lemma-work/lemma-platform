/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileSummaryResponse } from './FileSummaryResponse.js';
export type FileListResponse = {
    items: Array<FileSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
    total?: (number | null);
};
