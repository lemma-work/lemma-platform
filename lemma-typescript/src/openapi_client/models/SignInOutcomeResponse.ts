/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SignInOutcomeResponse = {
    origin: string;
    signed_in: boolean;
    /**
     * Whether the site stopped asking for a login straight afterwards. Reported, not enforced: the person has already done what was asked, and a site that shows a form at the same address under a neutral title reads as still asking.
     */
    working?: boolean;
};
