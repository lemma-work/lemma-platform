/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRunStatus } from './AgentRunStatus.js';
import type { LedgerTotals } from './LedgerTotals.js';
/**
 * One run of a conversation and what it cost.
 */
export type RunUsageResponse = {
    agent_run_id: string;
    finished_at?: (string | null);
    started_at: string;
    status: AgentRunStatus;
    /**
     * What the run cost and the tokens it used; null when nothing was recorded against it, which is not the same as nothing spent. A run on an Agent Host is recorded only if its harness reports usage, and only as each turn ends. A run still working reads what has been recorded so far.
     */
    usage?: (LedgerTotals | null);
};
