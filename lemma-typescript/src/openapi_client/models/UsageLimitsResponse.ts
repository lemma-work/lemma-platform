/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UsageLimitScopeResponse } from './UsageLimitScopeResponse.js';
export type UsageLimitsResponse = {
    allowed: boolean;
    org_monthly: UsageLimitScopeResponse;
    organization_id: (string | null);
    user_id: string;
    user_monthly: UsageLimitScopeResponse;
    user_weekly: UsageLimitScopeResponse;
};
