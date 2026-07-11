/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AppTriggerSummaryResponseSchema } from './AppTriggerSummaryResponseSchema.js';
/**
 * Schema for trigger list response.
 */
export type AppTriggerListResponseSchema = {
    items: Array<AppTriggerSummaryResponseSchema>;
    limit: number;
    next_page_token?: (string | null);
};
