/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { ConversationType } from './ConversationType.js';
export type CreateConversationRequest = {
    agent_name?: (string | null);
    agent_runtime?: (AgentRuntimeConfig | null);
    instructions?: (string | null);
    metadata?: (Record<string, any> | null);
    parent_id?: (string | null);
    /**
     * A GitHub repository to work in, as 'owner/name': one the organization's GitHub installation covers for you, as listed by the conversation repositories endpoint. The conversation's workspace_cwd is then that repository's checkout, cloned or fetched before the agent's first command. Every conversation of yours in the same repository works in that one checkout, uncommitted changes included.
     */
    repository?: (string | null);
    title?: (string | null);
    type?: ConversationType;
};
