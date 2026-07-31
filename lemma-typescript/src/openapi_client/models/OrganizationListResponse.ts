/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationResponse } from './OrganizationResponse.js';
/**
 * Organization list response with pagination.
 */
export type OrganizationListResponse = {
    items: Array<OrganizationResponse>;
    limit: number;
    next_page_token?: (string | null);
};
