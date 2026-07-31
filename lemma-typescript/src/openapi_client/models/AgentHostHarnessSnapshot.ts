/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostConfigOption } from './AgentHostConfigOption.js';
import type { AgentHostHarnessCapabilities } from './AgentHostHarnessCapabilities.js';
import type { AgentHostHarnessHealth } from './AgentHostHarnessHealth.js';
export type AgentHostHarnessSnapshot = {
    adapter_version: string;
    capabilities?: AgentHostHarnessCapabilities;
    config_options?: Array<AgentHostConfigOption>;
    config_revision: string;
    display_name: string;
    harness_key: string;
    health: AgentHostHarnessHealth;
    stale_after: string;
    stale_reason?: (string | null);
    upstream_version?: (string | null);
};
