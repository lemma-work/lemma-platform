/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodJoinRequestCreateResponse } from './PodJoinRequestCreateResponse.js';
export type PodJoinRequestListResponse = {
    items: Array<PodJoinRequestCreateResponse>;
    limit: number;
    next_page_token?: (string | null);
    total: number;
};
