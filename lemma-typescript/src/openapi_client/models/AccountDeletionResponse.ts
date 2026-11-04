/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What deleting the account did. Every session has ended by the time it is read.
 */
export type AccountDeletionResponse = {
    /**
     * Always true: the account no longer exists, whether this request deleted it or an earlier one had.
     */
    deleted: boolean;
    /**
     * Organizations the person was the only member of, deleted with their pods.
     */
    organizations_deleted: number;
    /**
     * Organizations others are still in, which the person left; their shared work stays there under "Deleted user".
     */
    organizations_left: number;
    pods_deleted: number;
};
