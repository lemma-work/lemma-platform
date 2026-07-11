/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorResponseSchema } from './ConnectorResponseSchema.js';
/**
 * Schema for connector list response.
 */
export type ConnectorListResponseSchema = {
    items: Array<ConnectorResponseSchema>;
    limit: number;
    next_page_token?: (string | null);
};
