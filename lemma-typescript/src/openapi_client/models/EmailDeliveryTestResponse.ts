/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type EmailDeliveryTestResponse = {
    /**
     * What happened, in words to show the person: where the email went, or what to change. Never the provider's own error.
     */
    message: string;
    /**
     * Whether the test email was handed to the provider
     */
    ok: boolean;
};
