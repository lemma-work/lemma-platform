/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrganizationRole } from './OrganizationRole.js';
import type { PodJoinRequestStatus } from './PodJoinRequestStatus.js';
import type { PodRole } from './PodRole.js';
export type PodJoinRequestCreateResponse = {
    approved_at?: (string | null);
    approved_by_user_id?: (string | null);
    created_at: string;
    id: string;
    org_role?: (OrganizationRole | null);
    organization_id: string;
    pod_id: string;
    pod_role?: (PodRole | null);
    requested_at: string;
    status: PodJoinRequestStatus;
    updated_at: string;
    user_email?: (string | null);
    user_id: string;
    user_name?: (string | null);
};
