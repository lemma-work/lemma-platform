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
    /**
     * Take the next free name instead of conflicting. For a name the user did not choose -- onboarding's derived first workspace -- where a 409 is a dead end for someone who never typed a name. Leave false for a name they typed, so a clash is reported.
     */
    resolve_name_conflicts?: boolean;
    slug?: (string | null);
};
