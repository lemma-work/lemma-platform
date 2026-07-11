/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
export type UpdateConversationRequest = {
    agent_runtime?: (AgentRuntimeConfig | null);
    instructions?: (string | null);
    metadata?: (Record<string, any> | null);
    title?: (string | null);
};
