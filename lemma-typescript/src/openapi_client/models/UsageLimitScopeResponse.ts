/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UsageLimitScopeResponse = {
    allowed: boolean;
    limit_usd?: (number | null);
    remaining_usd?: (number | null);
    reserved_usd: number;
    reset_at: string;
    scope: string;
    used_usd: number;
    window_start: string;
};
