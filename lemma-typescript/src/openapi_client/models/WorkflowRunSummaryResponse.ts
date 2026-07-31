/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunStatus } from './WorkflowRunStatus.js';
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
    updated_at?: (string | null);
    user_id: string;
    workflow_id: string;
};
