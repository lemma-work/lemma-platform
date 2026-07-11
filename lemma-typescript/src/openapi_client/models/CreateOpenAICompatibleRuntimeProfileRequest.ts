/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateOpenAICompatibleRuntimeProfileRequest = {
    api_key?: (string | null);
    base_url: string;
    default_model_name?: (string | null);
    description?: (string | null);
    headers?: Record<string, string>;
    model_names?: Array<string>;
    model_settings?: Record<string, any>;
    name: string;
    source?: string;
};
