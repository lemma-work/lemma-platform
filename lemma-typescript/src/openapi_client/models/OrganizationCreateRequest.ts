/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationJoinPolicy } from './OrganizationJoinPolicy.js';
/**
 * Organization creation request schema.
 */
export type OrganizationCreateRequest = {
    email_domain?: (string | null);
    join_policy?: OrganizationJoinPolicy;
    name: string;
    slug?: (string | null);
};
