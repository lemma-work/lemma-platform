/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionType } from './FunctionType.js';
import type { ResourceVisibility } from './ResourceVisibility.js';
/**
 * Request to update a function.
 */
export type UpdateFunctionRequest = {
    /**
     * Updated Python source for the function. When provided, the platform re-analyzes the code and refreshes input_schema, output_schema, and config_schema on the returned function.
     */
    code?: (string | null);
    config?: (Record<string, any> | null);
    description?: (string | null);
    icon_url?: (string | null);
    type?: (FunctionType | null);
    visibility?: (ResourceVisibility | null);
};
