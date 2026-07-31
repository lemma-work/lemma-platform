/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Status of a flow run.
 *
 * PENDING exists only in memory before the first advance; persisted runs
 * are RUNNING, WAITING, or terminal. WAITING is reserved for human form
 * waits. Runs suspended on platform work such as an agent, function job, or
 * timer remain RUNNING; the active wait row records the exact wait_type.
 */
export enum WorkflowRunStatus {
    PENDING = 'PENDING',
    RUNNING = 'RUNNING',
    WAITING = 'WAITING',
    COMPLETED = 'COMPLETED',
    FAILED = 'FAILED',
    CANCELLED = 'CANCELLED',
}
