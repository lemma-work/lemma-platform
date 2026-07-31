/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodMemberResponse } from './PodMemberResponse.js';
/**
 * Pod member list response.
 */
export type PodMemberListResponse = {
    items: Array<PodMemberResponse>;
    limit: number;
    next_page_token?: (string | null);
    total: number;
};
