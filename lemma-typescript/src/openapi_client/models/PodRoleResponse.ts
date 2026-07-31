/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Pod role response.
 */
export type PodRoleResponse = {
    created_at: string;
    created_by_user_id?: (string | null);
    description?: (string | null);
    id: string;
    is_system: boolean;
    name: string;
    organization_id?: (string | null);
    permission_ids?: Array<string>;
    pod_id: string;
};
