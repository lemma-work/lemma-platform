/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type GroupPersonResponse = {
    /**
     * Who they are to the pod -- ``member:<id>`` or ``contact:<id>`` -- the key a timeline line's ``author_external_id`` matches.
     */
    external_id?: (string | null);
    in_pod: boolean;
    name: string;
    user_id?: (string | null);
};
