/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceType } from './ResourceType.js';
export type ResourceAccessInviteCreateRequest = {
    email: string;
    permission_ids?: Array<string>;
    resource_id?: (string | null);
    resource_name?: (string | null);
    resource_type: ResourceType;
};
