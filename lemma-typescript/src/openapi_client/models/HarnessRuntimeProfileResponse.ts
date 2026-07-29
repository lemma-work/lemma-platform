/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HarnessRuntimeConfig } from './HarnessRuntimeConfig.js';
import type { RuntimeModelCatalogEntry } from './RuntimeModelCatalogEntry.js';
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
import type { RuntimeProfileStatus } from './RuntimeProfileStatus.js';
export type HarnessRuntimeProfileResponse = {
    availability_status?: (string | null);
    config: HarnessRuntimeConfig;
    default_model_name?: (string | null);
    description?: (string | null);
    harness_config_revision?: (string | null);
    harness_health?: (string | null);
    harness_id: string;
    harness_key?: (string | null);
    has_credentials?: boolean;
    host_display_name?: (string | null);
    host_id?: (string | null);
    host_status?: (string | null);
    id: string;
    model_catalog?: Array<RuntimeModelCatalogEntry>;
    name: string;
    organization_id?: (string | null);
    owner_user_id?: (string | null);
    runtime_type: 'HARNESS';
    scope: RuntimeProfileScope;
    status: RuntimeProfileStatus;
};
