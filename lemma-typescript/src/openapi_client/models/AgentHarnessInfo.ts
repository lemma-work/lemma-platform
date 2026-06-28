/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HarnessKind } from './HarnessKind.js';
import type { RuntimeModelCatalogEntry } from './RuntimeModelCatalogEntry.js';
export type AgentHarnessInfo = {
    availability_status?: (string | null);
    available?: boolean;
    daemon_display_name?: (string | null);
    daemon_id?: (string | null);
    daemon_status?: (string | null);
    display_name: string;
    harness_kind: HarnessKind;
    model_catalog?: Array<RuntimeModelCatalogEntry>;
    models?: Array<string>;
};

