/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UsageStatsBucketResponse = {
    bucket: string;
    cache_write_tokens?: number;
    cached_input_tokens?: number;
    group?: (string | null);
    input_tokens: number;
    output_tokens: number;
    system_cost_usd: number;
    total_cost_usd?: number;
    total_tokens: number;
    uncached_input_tokens?: number;
    units: number;
};
