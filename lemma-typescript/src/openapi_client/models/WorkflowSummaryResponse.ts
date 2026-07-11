/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowMode } from './WorkflowMode.js';
/**
 * Lean workflow shape for list responses.
 *
 * Omits the full graph (`nodes`/`edges`/`start`) — fetch those from
 * `workflow.get`. Carries cheap derived `node_count`/`node_types` so list
 * views can show step counts and participant badges without the graph.
 */
export type WorkflowSummaryResponse = {
    allowed_actions?: Array<string>;
    created_at?: (string | null);
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    is_active?: boolean;
    mode?: WorkflowMode;
    name: string;
    node_count?: number;
    node_types?: Array<string>;
    pod_id: string;
    updated_at?: (string | null);
    visibility?: string;
};
