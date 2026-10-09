/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SessionResponse = {
    /**
     * Send as `Authorization: Bearer` on every other call. Never store it.
     */
    access_token: string;
    display_name: (string | null);
    /**
     * Seconds until the access token expires.
     */
    expires_in: number;
    is_contact: boolean;
    /**
     * Only when new: keep it to come back. Null means keep the one you have.
     */
    secret: (string | null);
    title?: (string | null);
};
