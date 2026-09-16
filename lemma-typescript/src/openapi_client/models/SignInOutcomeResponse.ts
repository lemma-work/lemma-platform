/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SignInOutcomeResponse = {
    origin: string;
    /**
     * Whether the login was kept for next time.
     */
    saved?: boolean;
    /**
     * Why it was not kept, in words, when it was not.
     */
    saved_detail?: (string | null);
    signed_in: boolean;
};
