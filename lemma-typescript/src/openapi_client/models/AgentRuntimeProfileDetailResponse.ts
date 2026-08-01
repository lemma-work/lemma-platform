/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostHarnessResponse } from './AgentHostHarnessResponse.js';
import type { AgentHostStatus } from './AgentHostStatus.js';
import type { HarnessKind } from './HarnessKind.js';
import type { RuntimeModelCatalogEntry } from './RuntimeModelCatalogEntry.js';
import type { RuntimeProfileKind } from './RuntimeProfileKind.js';
import type { RuntimeProfileProtocol } from './RuntimeProfileProtocol.js';
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
import type { RuntimeProfileStatus } from './RuntimeProfileStatus.js';
/**
 * One profile plus the live harness it is bound to.
 *
 * An editor has to render the harness's *current* config options, not the ones
 * the profile was saved against - those are what the edit will be validated
 * and re-pinned to.
 */
export type AgentRuntimeProfileDetailResponse = {
    availability_status?: (string | null);
    config?: Record<string, any>;
    default_model_name?: (string | null);
    derived_harness_kind: HarnessKind;
    description?: (string | null);
    harness?: (AgentHostHarnessResponse | null);
    harness_id?: (string | null);
    has_credentials?: boolean;
    host_status?: (AgentHostStatus | null);
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
