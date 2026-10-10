/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where a follow-up goes.
 *
 * ``latest``: the contact's most recent conversation, on its own channel.
 * ``email``: their verified email address -- that conversation when it is
 * an email thread, a new thread from the pod's address when it is not.
 */
export enum FollowUpChannel {
    LATEST = 'latest',
    EMAIL = 'email',
}
