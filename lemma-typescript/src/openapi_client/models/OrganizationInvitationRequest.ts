/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationRole } from './OrganizationRole.js';
/**
 * Organization invitation request schema.
 */
export type OrganizationInvitationRequest = {
    email: string;
    /**
     * A few words from the inviter, quoted in the invitation email: why they are being asked in. The invitation record does not keep it; it travels with the email until the email is sent.
     */
    note?: (string | null);
    pod_id?: (string | null);
    pod_role?: (string | null);
    redirect_uri?: (string | null);
    role: OrganizationRole;
};
