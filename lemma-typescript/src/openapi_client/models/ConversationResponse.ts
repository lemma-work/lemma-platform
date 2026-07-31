/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRunStatus } from './AgentRunStatus.js';
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { ConversationStatus } from './ConversationStatus.js';
import type { ConversationType } from './ConversationType.js';
export type ConversationResponse = {
    agent_id?: (string | null);
    agent_runtime?: (AgentRuntimeConfig | null);
    created_at: string;
    id: string;
    instructions?: (string | null);
    last_run_error?: (string | null);
    last_run_finished_at?: (string | null);
    last_run_retryable?: boolean;
    last_run_status?: (AgentRunStatus | null);
    metadata?: (Record<string, any> | null);
    organization_id?: (string | null);
    output?: null;
    parent_id?: (string | null);
    pod_id: string;
    status?: (ConversationStatus | null);
    title?: (string | null);
    type?: ConversationType;
    updated_at: string;
    user_id: string;
};
