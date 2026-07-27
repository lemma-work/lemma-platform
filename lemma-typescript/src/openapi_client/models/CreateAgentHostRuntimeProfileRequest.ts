/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateAgentHostRuntimeProfileRequest = {
    config_selections?: Record<string, any>;
    description?: (string | null);
    fallback_profile_id?: (string | null);
    host_integration_id: string;
    host_wait_timeout_seconds?: number;
    integration_snapshot_revision: string;
    name: string;
    scope?: RuntimeProfileScope;
    source?: string;
};
