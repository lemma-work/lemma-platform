/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GoogleGenAIBackend } from './GoogleGenAIBackend.js';
/**
 * A Google provider: the Gemini API with an API key, or Vertex AI with a
 * service account key file. Never the server's own Google identity.
 */
export type CreateGoogleGenAIRuntimeProfileRequest = {
    api_key?: (string | null);
    backend?: GoogleGenAIBackend;
    default_model_name?: (string | null);
    description?: (string | null);
    image_model_names?: (Array<string> | null);
    model_names?: Array<string>;
    model_settings?: Record<string, any>;
    name: string;
    service_account_json?: (string | null);
    source?: string;
    vertex_location?: (string | null);
    vertex_project?: (string | null);
};
