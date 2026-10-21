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
    channel_id?: (string | null);
    created_at: string;
    /**
     * A question, approval or Agent Host permission request here is waiting on the conversation's owner. Not implied by status: a WAITING conversation may be waiting on a timer, and a permission request waits inside a RUNNING one.
     */
    has_pending_interaction?: boolean;
    id: string;
    instructions?: (string | null);
    is_archived?: boolean;
    last_activity_at: string;
    last_run_error?: (string | null);
    last_run_error_code?: (string | null);
    last_run_error_reason?: (string | null);
    last_run_finished_at?: (string | null);
    last_run_retryable?: boolean;
    last_run_status?: (AgentRunStatus | null);
    metadata?: (Record<string, any> | null);
    organization_id?: (string | null);
    output?: null;
    parent_id?: (string | null);
    /**
     * The conversation's working directory in pod files. Anything a person attaches here is what the agent finds by a bare filename, because this is the directory its pod tools resolve against.
     */
    readonly pod_cwd: string;
    pod_id: string;
    status?: (ConversationStatus | null);
    title?: (string | null);
    type?: ConversationType;
    updated_at: string;
    user_id: string;
    /**
     * WAITING on the agent's own work -- a sub-agent, a process or a timer -- rather than on a person: a run will start when it ends, so a viewer can follow the conversation to it. Set when one conversation is read, false in lists.
     */
    waiting_on_work?: boolean;
    /**
     * The conversation's working directory in the sandbox. This is where the agent's shell starts and where its files land, so it is the directory a file pane should be showing.
     */
    readonly workspace_cwd: string;
};
