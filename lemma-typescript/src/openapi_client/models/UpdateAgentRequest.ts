/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentPermissionsReplaceRequest } from './AgentPermissionsReplaceRequest.js';
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
    /**
     * Optional resource grants to REPLACE on this agent, in the same request. Equivalent to calling the permissions-replace endpoint right after update — grants are keyed by resource_name. Omit the key to leave existing grants alone; an empty grant list revokes them.
     */
    permissions?: (AgentPermissionsReplaceRequest | null);
    toolsets?: (Array<AgentToolset> | null);
    visibility?: (ResourceVisibility | null);
};
