/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationRole } from './OrganizationRole.js';
import type { UserResponse } from './UserResponse.js';
/**
 * Organization member response schema.
 */
export type OrganizationMemberResponse = {
    created_at: string;
    id: string;
    organization_id: string;
    role: OrganizationRole;
    updated_at: string;
    user?: (UserResponse | null);
    user_id: string;
};
