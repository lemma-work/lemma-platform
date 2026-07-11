/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountResponseSchema } from './AccountResponseSchema.js';
/**
 * Schema for account list response.
 */
export type AccountListResponseSchema = {
    items: Array<AccountResponseSchema>;
    limit: number;
    next_page_token?: (string | null);
};
