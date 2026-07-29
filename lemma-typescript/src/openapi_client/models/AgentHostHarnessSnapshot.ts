/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostAdapterProtocol } from './AgentHostAdapterProtocol.js';
import type { AgentHostConfigOption } from './AgentHostConfigOption.js';
import type { AgentHostHarnessCapabilities } from './AgentHostHarnessCapabilities.js';
import type { AgentHostHarnessHealth } from './AgentHostHarnessHealth.js';
export type AgentHostHarnessSnapshot = {
    adapter_protocol: AgentHostAdapterProtocol;
    adapter_protocol_version?: number;
    adapter_version: string;
    auth_state: string;
    capabilities?: AgentHostHarnessCapabilities;
    config_options?: Array<AgentHostConfigOption>;
    config_revision: string;
    display_name: string;
    fetched_at: string;
    harness_key: string;
    health: AgentHostHarnessHealth;
    metadata?: Record<string, any>;
    stale_after: string;
    stale_reason?: (string | null);
    upstream_version?: (string | null);
};
