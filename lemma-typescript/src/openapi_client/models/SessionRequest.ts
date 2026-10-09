/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SessionRequest = {
    /**
     * The solved challenge, when starting an anonymous session.
     */
    altcha?: (string | null);
    /**
     * A token the page's own server signed with the widget's secret.
     */
    host_token?: (string | null);
    /**
     * The secret a previous answer returned, to come back to that session.
     */
    secret?: (string | null);
};
