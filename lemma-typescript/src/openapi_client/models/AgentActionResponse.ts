/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { AgentToolset } from './AgentToolset.js';
export type AgentActionResponse = {
    agent_runtime?: (AgentRuntimeConfig | null);
    allowed_actions?: Array<string>;
    created_at: string;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    input_schema?: (Record<string, any> | null);
    instruction: string;
    metadata?: (Record<string, any> | null);
    name: string;
    output_schema?: (Record<string, any> | null);
    pod_id: string;
    toolsets?: Array<AgentToolset>;
    updated_at: string;
    user_id: string;
    visibility?: string;
};
