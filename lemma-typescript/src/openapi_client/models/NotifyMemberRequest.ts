/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Send a notification to one pod member.
 */
export type NotifyMemberRequest = {
    /**
     * Never shown to the recipient. Tells the agent that handles their reply what to do with it.
     */
    background_instruction?: (string | null);
    body: string;
    expects_response?: boolean;
    expires_in_seconds?: (number | null);
    /**
     * Pod member id, user id, or email address of the recipient.
     */
    recipient: string;
    title: string;
};
