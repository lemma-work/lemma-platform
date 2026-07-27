/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostCommand } from './AgentHostCommand.js';
import type { AgentHostStatus } from './AgentHostStatus.js';
export type AgentHostPollResponse = {
    commands?: Array<AgentHostCommand>;
    host_status: AgentHostStatus;
    policy_revision: string;
    poll_after_ms?: number;
    protocol_version?: number;
};
