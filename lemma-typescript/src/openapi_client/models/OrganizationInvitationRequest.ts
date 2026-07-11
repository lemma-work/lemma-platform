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
    pod_id?: (string | null);
    pod_role?: (string | null);
    redirect_uri?: (string | null);
    role: OrganizationRole;
};
