/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationMemberResponse } from './OrganizationMemberResponse.js';
/**
 * Organization member list response with pagination.
 */
export type OrganizationMemberListResponse = {
    items: Array<OrganizationMemberResponse>;
    limit: number;
    next_page_token?: (string | null);
};
