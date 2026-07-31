/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionResourcePermissionResponse } from './FunctionResourcePermissionResponse.js';
export type FunctionPermissionsResponse = {
    function_id: string;
    function_name: string;
    grants?: Array<FunctionResourcePermissionResponse>;
};
