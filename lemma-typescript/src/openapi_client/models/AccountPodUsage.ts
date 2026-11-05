/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodAccountSource } from './PodAccountSource.js';
import type { PodAccountVisibility } from './PodAccountVisibility.js';
export type AccountPodUsage = {
    is_member_default: boolean;
    last_used_at?: (string | null);
    pod_account_id: string;
    pod_id: string;
    source: PodAccountSource;
    used_by?: Array<string>;
    visibility: PodAccountVisibility;
};
