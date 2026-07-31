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
    title?: (string | null);
    type?: ConversationType;
};
