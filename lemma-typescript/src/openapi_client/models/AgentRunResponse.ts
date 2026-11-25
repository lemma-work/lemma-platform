/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRunStatus } from './AgentRunStatus.js';
import type { RunTokenUsage } from './RunTokenUsage.js';
/**
 * One run of a conversation: how it went, and the model tokens it used.
 */
export type AgentRunResponse = {
    agent_id?: (string | null);
    conversation_id: string;
    error?: (string | null);
    finished_at?: (string | null);
    id: string;
    output?: null;
    parent_run_id?: (string | null);
    started_at: string;
    status: AgentRunStatus;
    /**
     * Model tokens the run used, summed over its model calls; null when none were recorded against it. A run still working reads what it has used so far. Leaves out the conversation's title and the memory kept after the run, which land later. Tokens only: no cost (the conversation's usage route has it).
     */
    usage?: (RunTokenUsage | null);
};
