/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type UsageRecordResponse = {
    agent_id?: (string | null);
    agent_run_id?: (string | null);
    conversation_id?: (string | null);
    cost_usd?: (number | null);
    created_at: string;
    id: string;
    input_tokens: number;
    metadata: Record<string, any>;
    model_name: string;
    occurred_at: string;
    organization_id?: (string | null);
    output_tokens: number;
    parent_agent_run_id?: (string | null);
    pod_id?: (string | null);
    profile_id: string;
    profile_scope: string;
    source_id?: (string | null);
    source_type: string;
    status?: (string | null);
    total_tokens: number;
    units: number;
    usage_kind: string;
    user_id: string;
};
