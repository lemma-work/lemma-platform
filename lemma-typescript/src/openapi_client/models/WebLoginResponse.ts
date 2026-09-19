/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WebLoginResponse = {
    /**
     * How many cookies this site has. A rough sense of scale.
     */
    cookie_count: number;
    /**
     * When the soonest of them lapses, which is the closest thing to 'when will I have to sign in again'. Null when they are all session cookies, which go when the browser does.
     */
    expires?: (string | null);
    /**
     * The site, as a person would name it. Cookies are grouped by registrable domain, so `asur.work` and `api.asur.work` are one login rather than two -- the second being the half nobody visited on purpose.
     */
    site: string;
};
