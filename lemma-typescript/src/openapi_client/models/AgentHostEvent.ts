/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostEventType } from './AgentHostEventType.js';
export type AgentHostEvent = {
    event_id: string;
    lease_epoch: number;
    object_id?: (string | null);
    occurred_at: string;
    payload?: Record<string, any>;
    run_id: string;
    sequence: number;
    type: AgentHostEventType;
};
