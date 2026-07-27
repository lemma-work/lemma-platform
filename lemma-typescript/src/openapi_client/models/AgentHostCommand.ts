/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostCommandKind } from './AgentHostCommandKind.js';
export type AgentHostCommand = {
    command_id: string;
    created_at: string;
    expires_at: string;
    kind: AgentHostCommandKind;
    lease_epoch?: (number | null);
    payload?: Record<string, any>;
    payload_sha256: string;
    run_id?: (string | null);
};
