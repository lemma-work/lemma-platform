/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { StepRecordResponse } from './StepRecordResponse.js';
import type { WorkflowRunStatus } from './WorkflowRunStatus.js';
import type { WorkflowRunWaitResponse } from './WorkflowRunWaitResponse.js';
/**
 * Full run state. `execution_context` is the same flat view that
 * workflow expressions resolve against (`<node_id>.<field>`, `start.*`,
 * `loop.*`). `active_wait` is set when the run is suspended, including
 * WAITING form waits and RUNNING platform waits.
 */
export type WorkflowRunResponse = {
    active_wait?: (WorkflowRunWaitResponse | null);
    completed_at?: (string | null);
    created_at?: (string | null);
    current_node_id?: (string | null);
    error?: (string | null);
    execution_context?: Record<string, any>;
    failed_node_id?: (string | null);
    id: string;
    pod_id: string;
    schedule_event_id?: (string | null);
    start_type?: string;
    started_at?: (string | null);
    status?: WorkflowRunStatus;
    step_history?: Array<StepRecordResponse>;
    updated_at?: (string | null);
    user_id: string;
    workflow_id: string;
};
