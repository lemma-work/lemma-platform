/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What deleting the account did.
 *
 * By the time it is read no token of the person's opens anything; their
 * sessions and login are removed by work that commits with the deletion.
 */
export type AccountDeletionResponse = {
    /**
     * Always true: this request deleted the account. A request for an account that is already deleted never gets here; it is refused 403 ACCOUNT_INACTIVE.
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
