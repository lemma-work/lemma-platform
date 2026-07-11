/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNodeConfig } from './AgentNodeConfig.js';
/**
 * Agent node. The run waits on the agent conversation to complete.
 */
export type AgentNode = {
    config: AgentNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
