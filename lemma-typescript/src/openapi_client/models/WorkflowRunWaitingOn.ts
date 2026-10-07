/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunWaitType } from './WorkflowRunWaitType.js';
/**
 * Where a suspended run is parked and on whom -- the active wait, cut
 * down to what a list needs. The full wait (form schema included) is on
 * `workflow.run.get` as `active_wait`.
 */
export type WorkflowRunWaitingOn = {
    /**
     * The pod member a FORM wait is assigned to, when it is.
     */
    assigned_pod_member_id?: (string | null);
    node_id: string;
    /**
     * When the run started waiting here.
     */
    since?: (string | null);
    wait_type: WorkflowRunWaitType;
};
