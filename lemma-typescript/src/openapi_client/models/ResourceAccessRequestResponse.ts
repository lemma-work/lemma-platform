/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ResourceAccessRequestResponse = {
    decided_at?: (string | null);
    decided_by_user_id?: (string | null);
    id: string;
    message?: (string | null);
    pod_id: string;
    requested_at: string;
    requested_permission_ids?: Array<string>;
    requester_email?: (string | null);
    requester_name?: (string | null);
    requester_user_id: string;
    resource_id: string;
    resource_name?: (string | null);
    resource_type: string;
    status: string;
};
