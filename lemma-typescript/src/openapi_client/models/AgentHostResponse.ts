/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostStatus } from './AgentHostStatus.js';
export type AgentHostResponse = {
    capacity: Record<string, any>;
    created_at: string;
    display_name: string;
    host_release: string;
    id: string;
    installation_id: string;
    last_seen_at: (string | null);
    protocol_version: (number | null);
    revoked_at: (string | null);
    status: AgentHostStatus;
    updated_at: string;
    user_id: string;
};
