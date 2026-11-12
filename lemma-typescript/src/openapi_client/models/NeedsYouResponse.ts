/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NeedsYouItemResponse } from './NeedsYouItemResponse.js';
export type NeedsYouResponse = {
    /**
     * Oldest first: what has waited longest is listed first.
     */
    items: Array<NeedsYouItemResponse>;
    limit: number;
    /**
     * More was waiting than `limit`; the oldest are listed.
     */
    truncated: boolean;
};
