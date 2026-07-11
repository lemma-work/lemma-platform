/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentToolset } from './AgentToolset.js';
/**
 * Lean agent shape for list responses.
 *
 * Omits the heavy single-resource fields (`instruction`, `input_schema`,
 * `output_schema`, `agent_runtime`) — fetch those from `agent.get`. Keeps
 * `toolsets` so list cards can show a connection count.
 */
export type AgentSummaryResponse = {
    allowed_actions?: Array<string>;
    created_at: string;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    metadata?: (Record<string, any> | null);
    name: string;
    pod_id: string;
    toolsets?: Array<AgentToolset>;
    updated_at: string;
    user_id: string;
    visibility?: string;
};
