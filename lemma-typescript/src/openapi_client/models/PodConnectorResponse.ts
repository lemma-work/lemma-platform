/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConnectorCoverage } from './PodConnectorCoverage.js';
import type { PodConnectorMode } from './PodConnectorMode.js';
import type { PodConnectorSource } from './PodConnectorSource.js';
import type { PodConnectorStatus } from './PodConnectorStatus.js';
import type { PodConnectorVisibility } from './PodConnectorVisibility.js';
import type { SharedAccountSummary } from './SharedAccountSummary.js';
export type PodConnectorResponse = {
    auth_config_id: string;
    /**
     * Whether the caller may run operations through it: what decides if connecting their own account would let them use it. False for a viewer, and for anyone a RESTRICTED connector leaves out.
     */
    can_use?: boolean;
    connector_id?: (string | null);
    created_at: string;
    created_by_user_id?: (string | null);
    id: string;
    installation_name?: (string | null);
    /**
     * Not filled in on listings, which stay cheap: read it from `GET /pods/{pod_id}/connectors/{slug}/coverage`. Kept so a client reading it gets null rather than a missing key.
     */
    member_coverage?: (PodConnectorCoverage | null);
    mode: PodConnectorMode;
    pod_id: string;
    purpose?: (string | null);
    required: boolean;
    shared_account?: (SharedAccountSummary | null);
    shared_pod_account_id?: (string | null);
    slug: string;
    source?: PodConnectorSource;
    status: PodConnectorStatus;
    updated_at: string;
    visibility: PodConnectorVisibility;
};
