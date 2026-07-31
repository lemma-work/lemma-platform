/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Result of staging a local .zip: a signed URL to feed the URL-based import.
 */
export type UploadResponse = {
    expires_at: string;
    /**
     * Signed lemma download URL (pass as kind=URL).
     */
    url: string;
};
