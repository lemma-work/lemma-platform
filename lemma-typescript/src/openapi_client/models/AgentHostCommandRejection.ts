/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostRejectionCode } from './AgentHostRejectionCode.js';
export type AgentHostCommandRejection = {
    code: AgentHostRejectionCode;
    command_id: string;
    detail?: (string | null);
    lease_epoch: number;
    retryable: boolean;
    run_id: string;
};
