/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One thing that was done with one saved login.
 *
 * `detail` is why, when the outcome was not plain "ok" — "session rejected",
 * "nothing for this site was in the browser". It is the platform's own words
 * rather than an agent's paraphrase, which is the point of reading it here.
 */
export type WebLoginAuditEntry = {
    action: string;
    /**
     * The run that did it, or null for something the person did themselves from the saved-logins screen.
     */
    conversation_id: (string | null);
    created_at: string;
    detail: (string | null);
    origin: string;
    outcome: string;
};
