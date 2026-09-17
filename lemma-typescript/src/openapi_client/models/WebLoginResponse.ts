/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WebLoginResponse = {
    created_at: string;
    id: string;
    last_used_at: (string | null);
    origin: string;
    updated_at: string;
    /**
     * Whether the stored session still signs you in. False means it stopped working and the next run will ask you again.
     */
    working: boolean;
};
