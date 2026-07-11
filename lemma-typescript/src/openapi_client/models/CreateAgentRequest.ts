/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentPermissionsReplaceRequest } from './AgentPermissionsReplaceRequest.js';
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { AgentToolset } from './AgentToolset.js';
import type { ResourceVisibility } from './ResourceVisibility.js';
export type CreateAgentRequest = {
    agent_runtime?: (AgentRuntimeConfig | null);
    description?: (string | null);
    icon_url?: (string | null);
    input_schema?: (Record<string, any> | null);
    instruction: string;
    metadata?: (Record<string, any> | null);
    name: string;
    output_schema?: (Record<string, any> | null);
    /**
     * Optional resource grants to apply to the new agent in the same request. Equivalent to calling the permissions-replace endpoint right after create — grants are keyed by resource_name.
     */
    permissions?: (AgentPermissionsReplaceRequest | null);
    toolsets?: Array<AgentToolset>;
    visibility?: ResourceVisibility;
};
