/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type MessageRequest = {
    /**
     * The page's own name for this message, echoed on it in history, so the page can tell its copy from the server's without comparing text.
     */
    client_nonce?: (string | null);
    text: string;
};
