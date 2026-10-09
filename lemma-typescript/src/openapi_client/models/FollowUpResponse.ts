/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FollowUpResponse = {
    conversation_id: string;
    /**
     * Handed to the platform now. False for a web chat, where the message waits for the contact's next visit.
     */
    delivered: boolean;
    platform: string;
};
