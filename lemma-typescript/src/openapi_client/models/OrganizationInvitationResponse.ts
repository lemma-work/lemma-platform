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
    /**
     * The link the invitee opens to accept, as the invitation email carries it. Returned to the organization's owners and editors, so an invitation can be handed over another way when email is not set up here.
     */
    accept_url?: (string | null);
    accepted_at?: (string | null);
    created_at: string;
    email: string;
    /**
     * Whether this server emails invitations. False means email is not set up here: the invitation exists, but nobody was sent it, so share `accept_url` with the invitee yourself.
     */
    emailed?: (boolean | null);
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
