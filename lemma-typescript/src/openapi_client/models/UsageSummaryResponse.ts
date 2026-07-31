/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UsageSummaryResponse = {
    agent_id?: (string | null);
    end_date: string;
    organization_id?: (string | null);
    period_days: number;
    pod_id?: (string | null);
    start_date: string;
    system_cost_usd: number;
    total_by_kind: Record<string, Record<string, any>>;
    total_by_model: Record<string, Record<string, any>>;
    total_by_profile: Record<string, Record<string, any>>;
    total_input_tokens: number;
    total_output_tokens: number;
    total_tokens: number;
    total_units: number;
    user_id?: (string | null);
};
