/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileStatus } from './RuntimeProfileStatus.js';
export type UpdateRuntimeProfileRequest = {
    api_key?: (string | null);
    api_version?: (string | null);
    azure_endpoint?: (string | null);
    base_url?: (string | null);
    config_selections?: (Record<string, any> | null);
    default_model_name?: (string | null);
    description?: (string | null);
    fallback_profile_id?: (string | null);
    harness_snapshot_revision?: (string | null);
    headers?: (Record<string, string> | null);
    host_wait_timeout_seconds?: (number | null);
    location?: (string | null);
    model_names?: (Array<string> | null);
    model_settings?: (Record<string, any> | null);
    name?: (string | null);
    project_id?: (string | null);
    service_account_json?: (Record<string, any> | null);
    status?: (RuntimeProfileStatus | null);
};
