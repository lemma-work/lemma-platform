/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeProfileScope } from './RuntimeProfileScope.js';
export type CreateAgentHostRuntimeProfileRequest = {
    config_selections?: Record<string, any>;
    default_model_name?: (string | null);
    description?: (string | null);
    harness_id: string;
    name: string;
    scope?: RuntimeProfileScope;
    source?: string;
};
