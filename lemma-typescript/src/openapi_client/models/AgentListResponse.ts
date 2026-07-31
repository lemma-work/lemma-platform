/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSummaryResponse } from './AgentSummaryResponse.js';
export type AgentListResponse = {
    items: Array<AgentSummaryResponse>;
    limit: number;
    next_page_token?: (string | null);
};
