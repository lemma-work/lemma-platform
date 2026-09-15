/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AnswerSignInRequest = {
    /**
     * Save whatever the browser holds even though it does not look signed in. For sites the check reads wrongly.
     */
    force?: boolean;
    /**
     * True when the person says they have signed in; false when they cannot right now.
     */
    signed_in: boolean;
};
