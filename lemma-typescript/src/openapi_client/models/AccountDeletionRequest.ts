/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Proof that the person means this account, typed back by them.
 */
export type AccountDeletionRequest = {
    /**
     * The account's own email address, typed by the person. Compared case-insensitively; anything else is refused with ACCOUNT_DELETION_CONFIRMATION_MISMATCH.
     */
    confirm_email: string;
};
