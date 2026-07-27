/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostCapacity } from './AgentHostCapacity.js';
import type { AgentHostRunCheckpoint } from './AgentHostRunCheckpoint.js';
import type { HostHello } from './HostHello.js';
export type AgentHostPollRequest = {
    acknowledged_command_ids?: Array<string>;
    capacity?: AgentHostCapacity;
    checkpoints?: Array<AgentHostRunCheckpoint>;
    hello: HostHello;
};
