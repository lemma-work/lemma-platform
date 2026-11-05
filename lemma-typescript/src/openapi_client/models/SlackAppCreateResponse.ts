/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The app Slack made, and the install to authorise it against.
 */
export type SlackAppCreateResponse = {
    app_id: string;
    auth_config_id: string;
    /**
     * An app made earlier for the same agent and workspace (or the same Idempotency-Key), not a new one.
     */
    reused?: boolean;
};
