/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationJoinPolicy } from './OrganizationJoinPolicy.js';
/**
 * Organization update request schema (owner-only).
 */
export type OrganizationUpdateRequest = {
    email_domain?: (string | null);
    join_policy?: (OrganizationJoinPolicy | null);
    name?: (string | null);
};
