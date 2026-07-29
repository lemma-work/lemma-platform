/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateAzureOpenAIRuntimeProfileRequest = {
    api_key: string;
    api_version?: (string | null);
    azure_endpoint: string;
    default_model_name: string;
    description?: (string | null);
    model_names: Array<string>;
    model_settings?: Record<string, any>;
    name: string;
    runtime_type: string;
    scope?: RuntimeProfileScope;
};
