/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SurfaceSendResponse = {
    /**
     * Where it went: `chat`, or `email` when the chat's reply window had closed and it was sent to the member's email address instead.
     */
    channel?: (string | null);
    /**
     * Why it went where it did.
     */
    detail?: (string | null);
    sent: boolean;
};
