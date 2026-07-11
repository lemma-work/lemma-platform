/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationInvitationStatus } from './OrganizationInvitationStatus.js';
import type { OrganizationRole } from './OrganizationRole.js';
/**
 * Organization invitation response schema.
 */
export type OrganizationInvitationResponse = {
    accepted_at?: (string | null);
    created_at: string;
    email: string;
    expires_at: string;
    id: string;
    organization_id: string;
    organization_name?: (string | null);
    pod_description?: (string | null);
    pod_id?: (string | null);
    pod_name?: (string | null);
    pod_role?: (string | null);
    redirect_uri?: (string | null);
    revoked_at?: (string | null);
    role: OrganizationRole;
    status: OrganizationInvitationStatus;
    updated_at: string;
};
