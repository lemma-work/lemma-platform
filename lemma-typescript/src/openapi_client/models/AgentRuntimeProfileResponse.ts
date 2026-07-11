/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HarnessKind } from './HarnessKind.js';
import type { RuntimeModelCatalogEntry } from './RuntimeModelCatalogEntry.js';
import type { RuntimeProfileKind } from './RuntimeProfileKind.js';
import type { RuntimeProfileProtocol } from './RuntimeProfileProtocol.js';
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
import type { RuntimeProfileStatus } from './RuntimeProfileStatus.js';
export type AgentRuntimeProfileResponse = {
    availability_status?: (string | null);
    config?: Record<string, any>;
    daemon_display_name?: (string | null);
    daemon_harness_available?: (boolean | null);
    daemon_id?: (string | null);
    daemon_status?: (string | null);
    default_model_name?: (string | null);
    derived_harness_kind: HarnessKind;
    description?: (string | null);
    has_credentials?: boolean;
    id: string;
    kind: RuntimeProfileKind;
    metadata?: Record<string, any>;
    model_catalog?: Array<RuntimeModelCatalogEntry>;
    name: string;
    organization_id?: (string | null);
    protocol: RuntimeProfileProtocol;
    scope: RuntimeProfileScope;
    status: RuntimeProfileStatus;
    user_id?: (string | null);
};
