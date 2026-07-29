/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateHarnessRuntimeProfileRequest = {
    config_selections?: Record<string, any>;
    default_model_name?: (string | null);
    description?: (string | null);
    fallback_profile_id?: (string | null);
    harness_id: string;
    harness_snapshot_revision: string;
    host_wait_timeout_seconds?: number;
    name: string;
    runtime_type: string;
    scope?: RuntimeProfileScope;
};
