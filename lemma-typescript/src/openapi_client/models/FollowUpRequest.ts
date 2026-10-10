/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FollowUpChannel } from './FollowUpChannel.js';
export type FollowUpRequest = {
    /**
     * `latest` writes in the contact's most recent conversation. `email` sends to their verified email address: in that conversation when it is an email thread, otherwise in a new thread from the pod's email address that answers contacts.
     */
    channel?: FollowUpChannel;
    message: string;
};
