/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Omitted keeps a field. The host (``backend``) is not editable: moving
 * between the Gemini API and Vertex AI is a new provider.
 */
export type UpdateGoogleGenAIRuntimeProfileRequest = {
    api_key?: (string | null);
    default_model_name?: (string | null);
    description?: (string | null);
    image_model_names?: (Array<string> | null);
    model_names?: (Array<string> | null);
    model_settings?: (Record<string, any> | null);
    name?: (string | null);
    service_account_json?: (string | null);
    source?: string;
    vertex_location?: (string | null);
    vertex_project?: (string | null);
};
