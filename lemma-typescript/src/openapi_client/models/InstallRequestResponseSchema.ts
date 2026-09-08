/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where to send somebody to finish installing.
 *
 * Only the URL: like a connect request, the `state` on it is a capability and
 * does not belong in a response body, browser memory or a HAR capture.
 */
export type InstallRequestResponseSchema = {
    /**
     * Provider URL that completes the installation step.
     */
    authorization_url: string;
};
