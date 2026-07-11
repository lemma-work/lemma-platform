/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunWaitStatus } from './WorkflowRunWaitStatus.js';
import type { WorkflowRunWaitType } from './WorkflowRunWaitType.js';
export type WorkflowRunWaitResponse = {
    assigned_pod_member_id?: (string | null);
    completed_at?: (string | null);
    created_at?: (string | null);
    external_ref?: (string | null);
    id: string;
    node_id: string;
    payload?: Record<string, any>;
    pod_id: string;
    run_id: string;
    status: WorkflowRunWaitStatus;
    wait_type: WorkflowRunWaitType;
    workflow_id: string;
};
