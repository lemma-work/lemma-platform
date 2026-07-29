/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GoogleVertexRuntimeConfig } from './GoogleVertexRuntimeConfig.js';
import type { RuntimeModelCatalogEntry } from './RuntimeModelCatalogEntry.js';
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
import type { RuntimeProfileStatus } from './RuntimeProfileStatus.js';
export type GoogleVertexRuntimeProfileResponse = {
    availability_status?: (string | null);
    config: (GoogleVertexRuntimeConfig | null);
    default_model_name?: (string | null);
    description?: (string | null);
    has_credentials?: boolean;
    id: string;
    model_catalog?: Array<RuntimeModelCatalogEntry>;
    name: string;
    organization_id?: (string | null);
    owner_user_id?: (string | null);
    runtime_type: 'GOOGLE_VERTEX';
    scope: RuntimeProfileScope;
    status: RuntimeProfileStatus;
};
