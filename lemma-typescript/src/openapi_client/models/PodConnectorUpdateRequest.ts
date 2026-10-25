/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConnectorMode } from './PodConnectorMode.js';
import type { PodConnectorVisibility } from './PodConnectorVisibility.js';
/**
 * Fields left out are unchanged; `shared_pod_account_id: null` clears it.
 */
export type PodConnectorUpdateRequest = {
    mode?: (PodConnectorMode | null);
    purpose?: (string | null);
    required?: (boolean | null);
    shared_pod_account_id?: (string | null);
    visibility?: (PodConnectorVisibility | null);
};
