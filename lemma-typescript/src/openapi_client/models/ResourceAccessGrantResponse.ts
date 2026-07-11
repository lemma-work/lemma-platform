/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceType } from './ResourceType.js';
export type ResourceAccessGrantResponse = {
    display_name?: (string | null);
    email?: (string | null);
    grantee_id: string;
    grantee_type: string;
    permission_ids?: Array<string>;
    resource_name: string;
    resource_type: ResourceType;
    role_name?: (string | null);
    user_id?: (string | null);
};
