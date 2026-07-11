/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceAccessGrantResponse } from './ResourceAccessGrantResponse.js';
import type { ResourceType } from './ResourceType.js';
export type ResourceAccessResponse = {
    grants?: Array<ResourceAccessGrantResponse>;
    resource_name: string;
    resource_type: ResourceType;
};
