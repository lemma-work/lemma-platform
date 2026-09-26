/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type EmailDeliveryStatusResponse = {
    /**
     * Whether mail sent from this server can reach an inbox. False while no provider is set up, and while mail is written to a local spool (EMAIL_TRANSPORT=filesystem) instead of being sent.
     */
    configured: boolean;
};
