/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceType } from './ResourceType.js';
/**
 * What a shared link may disclose about its target.
 *
 * Returned only when the viewer can actually read the resource, so every field
 * here is something they could already see by opening it.
 */
export type ResourcePreviewResponse = {
    allowed_actions?: Array<string>;
    owner_user_id?: (string | null);
    pod_id: string;
    resource_id?: (string | null);
    resource_name?: (string | null);
    resource_type: ResourceType;
    visibility?: (string | null);
};
