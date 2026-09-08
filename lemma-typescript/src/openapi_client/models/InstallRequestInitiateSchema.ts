/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which account still needs its provider-side installation.
 */
export type InstallRequestInitiateSchema = {
    /**
     * The connected account the installation is for.
     */
    account_id: string;
    /**
     * Path inside the app to come back to. Only a rooted path is accepted; anything else is ignored rather than followed.
     */
    return_to?: (string | null);
};
