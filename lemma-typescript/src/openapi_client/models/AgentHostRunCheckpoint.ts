/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostRunState } from './AgentHostRunState.js';
export type AgentHostRunCheckpoint = {
    detail?: Record<string, any>;
    lease_epoch: number;
    run_id: string;
    state: AgentHostRunState;
};
