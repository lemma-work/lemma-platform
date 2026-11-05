/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GranteeRef } from './GranteeRef.js';
import type { PodAccountVisibility } from './PodAccountVisibility.js';
export type PodAccountCreateRequest = {
    /**
     * One of your own accounts in this organization.
     */
    account_id: string;
    /**
     * RESTRICTED only: the members or roles who may use it.
     */
    share_with?: Array<GranteeRef>;
    visibility?: PodAccountVisibility;
};
