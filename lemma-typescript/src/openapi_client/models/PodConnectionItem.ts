/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectionCoverage } from './ConnectionCoverage.js';
import type { ConnectionMe } from './ConnectionMe.js';
import type { ConnectionRunsAs } from './ConnectionRunsAs.js';
import type { PodConnectorMode } from './PodConnectorMode.js';
import type { PodConnectorStatus } from './PodConnectorStatus.js';
export type PodConnectionItem = {
    /**
     * The organization installation; what a connect request names.
     */
    auth_config_id: string;
    connector_id?: (string | null);
    coverage?: (ConnectionCoverage | null);
    icon?: (string | null);
    installation_name?: (string | null);
    me: ConnectionMe;
    mode: PodConnectorMode;
    purpose?: (string | null);
    required?: boolean;
    runs_as: ConnectionRunsAs;
    slug: string;
    status: PodConnectorStatus;
    title?: (string | null);
};
