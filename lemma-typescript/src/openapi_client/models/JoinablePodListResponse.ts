/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JoinablePodResponse } from './JoinablePodResponse.js';
/**
 * Pods the caller may join, a page at a time.
 */
export type JoinablePodListResponse = {
    items: Array<JoinablePodResponse>;
    limit: number;
    next_page_token?: (string | null);
    total: number;
};
