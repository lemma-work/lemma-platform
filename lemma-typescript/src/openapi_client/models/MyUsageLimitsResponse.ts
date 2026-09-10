/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UsageAllowanceResponse } from './UsageAllowanceResponse.js';
export type MyUsageLimitsResponse = {
    allowed: boolean;
    organization_id: (string | null);
    plan_name: (string | null);
    plan_type: ('PERSONAL' | 'TEAM' | null);
    warning_percent: number;
    windows: Array<UsageAllowanceResponse>;
};
