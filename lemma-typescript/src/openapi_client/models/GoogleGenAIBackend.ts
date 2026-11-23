/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which host answers the Google Gen AI API, and so how it is signed in to.
 *
 * ``GEMINI_API`` takes an API key. ``VERTEX_AI`` takes a service account --
 * a stored key file, or for the deployment's own profile, the identity the
 * server already runs as (Application Default Credentials).
 */
export enum GoogleGenAIBackend {
    GEMINI_API = 'GEMINI_API',
    VERTEX_AI = 'VERTEX_AI',
}
