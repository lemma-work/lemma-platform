/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { AgentToolset } from './AgentToolset.js';
import type { ResourceVisibility } from './ResourceVisibility.js';
export type UpdateAgentRequest = {
    agent_runtime?: (AgentRuntimeConfig | null);
    description?: (string | null);
    icon_url?: (string | null);
    input_schema?: (Record<string, any> | null);
    instruction?: (string | null);
    metadata?: (Record<string, any> | null);
    output_schema?: (Record<string, any> | null);
    toolsets?: (Array<AgentToolset> | null);
    visibility?: (ResourceVisibility | null);
};
