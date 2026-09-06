/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UsageAllowanceResponse } from './UsageAllowanceResponse.js';
export type MyUsageLimitsResponse = {
    allowed: boolean;
    organization_id: (string | null);
    payer: ('personal' | 'organization' | null);
    plan_name: (string | null);
    warning_percent: number;
    windows: Array<UsageAllowanceResponse>;
};
