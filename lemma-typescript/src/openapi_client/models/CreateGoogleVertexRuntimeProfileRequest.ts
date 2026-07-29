/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateGoogleVertexRuntimeProfileRequest = {
    default_model_name: string;
    description?: (string | null);
    location: string;
    model_names: Array<string>;
    model_settings?: Record<string, any>;
    name: string;
    project_id: string;
    runtime_type: string;
    scope?: RuntimeProfileScope;
    service_account_json?: (Record<string, any> | null);
};
