/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UsageStatsBucketResponse } from './UsageStatsBucketResponse.js';
export type UsageStatsResponse = {
    end_date: string;
    granularity: string;
    group_by?: (string | null);
    items: Array<UsageStatsBucketResponse>;
    start_date: string;
    total: number;
};
