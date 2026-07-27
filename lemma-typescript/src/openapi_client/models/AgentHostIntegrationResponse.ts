/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentHostIntegrationResponse = {
    adapter_protocol: string;
    adapter_version: string;
    auth_state: string;
    capabilities: Record<string, any>;
    config_options: Array<any>;
    config_revision: string;
    display_name: string;
    fetched_at: string;
    health: string;
    host_id: string;
    id: string;
    integration_key: string;
    metadata: Record<string, any>;
    stale_after: string;
    stale_reason: (string | null);
    upstream_version: (string | null);
};
