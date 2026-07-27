/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostCheckpoint } from './AgentHostCheckpoint.js';
import type { AgentHostRunState } from './AgentHostRunState.js';
export type AgentHostRunCheckpoint = {
    checkpoint: AgentHostCheckpoint;
    detail?: Record<string, any>;
    lease_epoch: number;
    run_id: string;
    state: AgentHostRunState;
};
