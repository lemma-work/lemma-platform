/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ApprovePairingRequest = {
    /**
     * The code it shows.
     */
    code: string;
    /**
     * Its name in refs; defaults to one made from its own.
     */
    name?: (string | null);
    /**
     * Share it into this pod (editors and admins).
     */
    pod_id?: (string | null);
};
