/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ResourceAccessInviteResponse = {
    email: string;
    id: string;
    invited_at: string;
    invited_by_user_id?: (string | null);
    permission_ids?: Array<string>;
    pod_id: string;
    redeemed_at?: (string | null);
    resource_id: string;
    resource_name?: (string | null);
    resource_type: string;
    status: string;
};
