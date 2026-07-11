/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationJoinPolicy } from './OrganizationJoinPolicy.js';
/**
 * Organization response schema.
 */
export type OrganizationResponse = {
    created_at: string;
    email_domain?: (string | null);
    id: string;
    join_policy: OrganizationJoinPolicy;
    name: string;
    slug: string;
    updated_at: string;
};
