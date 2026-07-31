/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppDetailResponse } from './AppDetailResponse.js';
export type AppListResponse = {
    items: Array<AppDetailResponse>;
    limit: number;
    next_page_token?: (string | null);
};
