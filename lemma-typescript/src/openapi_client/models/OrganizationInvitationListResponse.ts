/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationInvitationResponse } from './OrganizationInvitationResponse.js';
/**
 * Organization invitation list response with pagination.
 */
export type OrganizationInvitationListResponse = {
    items: Array<OrganizationInvitationResponse>;
    limit: number;
    next_page_token?: (string | null);
};
