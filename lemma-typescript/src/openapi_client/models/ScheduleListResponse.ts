/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleDetailResponse } from './ScheduleDetailResponse.js';
/**
 * Schedule list response.
 */
export type ScheduleListResponse = {
    items: Array<ScheduleDetailResponse>;
    limit: number;
    next_page_token?: (string | null);
};
