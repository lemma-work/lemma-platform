/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateAnthropicCompatibleRuntimeProfileRequest = {
    api_key: string;
    base_url?: (string | null);
    default_model_name?: (string | null);
    description?: (string | null);
    headers?: Record<string, string>;
    model_names?: Array<string>;
    model_settings?: Record<string, any>;
    name: string;
    runtime_type: string;
    scope?: RuntimeProfileScope;
};
