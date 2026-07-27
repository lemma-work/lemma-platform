/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostAdapterProtocol } from './AgentHostAdapterProtocol.js';
import type { AgentHostConfigOption } from './AgentHostConfigOption.js';
import type { AgentHostIntegrationCapabilities } from './AgentHostIntegrationCapabilities.js';
import type { AgentHostIntegrationHealth } from './AgentHostIntegrationHealth.js';
export type AgentHostIntegrationSnapshot = {
    adapter_protocol: AgentHostAdapterProtocol;
    adapter_version: string;
    auth_state: string;
    capabilities?: AgentHostIntegrationCapabilities;
    config_options?: Array<AgentHostConfigOption>;
    config_revision: string;
    display_name: string;
    fetched_at: string;
    health: AgentHostIntegrationHealth;
    integration_key: string;
    metadata?: Record<string, any>;
    stale_after: string;
    stale_reason?: (string | null);
    upstream_version?: (string | null);
};
