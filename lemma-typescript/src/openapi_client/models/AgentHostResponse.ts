/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostStatus } from './AgentHostStatus.js';
export type AgentHostResponse = {
    adapter_manifest_id: string;
    capacity: Record<string, any>;
    created_at: string;
    display_name: string;
    host_release: string;
    id: string;
    installation_id: string;
    instance_id: (string | null);
    last_seen_at: (string | null);
    organization_id: (string | null);
    protocol_max: number;
    protocol_min: number;
    protocol_version: (number | null);
    public_key_fingerprint: string;
    revoked_at: (string | null);
    status: AgentHostStatus;
    updated_at: string;
    user_id: string;
};
