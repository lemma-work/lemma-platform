/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunStatus } from './WorkflowRunStatus.js';
import type { WorkflowRunWaitingOn } from './WorkflowRunWaitingOn.js';
export type WorkflowRunSummaryResponse = {
    completed_at?: (string | null);
    created_at?: (string | null);
    current_node_id?: (string | null);
    error?: (string | null);
    failed_node_id?: (string | null);
    id: string;
    pod_id: string;
    schedule_event_id?: (string | null);
    start_type?: string;
    started_at?: (string | null);
    status?: WorkflowRunStatus;
    /**
     * What this run is about, from the workflow's `run_title`. Null when the workflow sets none or nothing it names is filled in yet.
     */
    title?: (string | null);
    updated_at?: (string | null);
    user_id: string;
    /**
     * The run's active wait, while it has one.
     */
    waiting_on?: (WorkflowRunWaitingOn | null);
    workflow_id: string;
};
