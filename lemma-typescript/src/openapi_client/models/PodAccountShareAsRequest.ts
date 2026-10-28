/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Share one of your pod accounts with the pod as a SHARED connector.
 */
export type PodAccountShareAsRequest = {
    purpose?: (string | null);
    /**
     * The SHARED connector to run as it: an existing one on the same installation is pointed at it, otherwise one is declared.
     */
    slug: string;
};
