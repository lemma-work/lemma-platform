/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LedgerTotals } from './LedgerTotals.js';
import type { RunUsageResponse } from './RunUsageResponse.js';
/**
 * What one conversation has cost its owner so far.
 */
export type ConversationUsageResponse = {
    conversation_id: string;
    /**
     * Whether older runs exist than `runs` lists; `totals` has them.
     */
    has_more_runs: boolean;
    /**
     * Every run of the conversation.
     */
    run_count: number;
    /**
     * The newest runs, newest first, each with its own usage.
     */
    runs: Array<RunUsageResponse>;
    /**
     * Summed over every run of the conversation, the title and the memory kept after a run included. A sub-agent's conversation is its own: list them with `parent_id` and read each one's usage.
     */
    totals: LedgerTotals;
    /**
     * Runs with nothing recorded against them. Above zero, `totals` leaves those runs out: show the total as incomplete, not as everything spent.
     */
    unreported_run_count: number;
};
