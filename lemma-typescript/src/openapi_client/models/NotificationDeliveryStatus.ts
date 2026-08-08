/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where the *channel* is: did the message physically get to them?
 *
 * Deliberately a second column rather than more members on
 * :class:`NotificationStatus`. The two axes are independent — a notification
 * can be DELIVERED and still OPEN (they haven't answered), or UNDELIVERABLE
 * and still RESPONDED (they saw it in the app and replied there). Smearing
 * them into one enum is how you end up unable to answer "who did we fail to
 * reach?", which is the only question this column exists for.
 */
export enum NotificationDeliveryStatus {
    PENDING = 'PENDING',
    DELIVERED = 'DELIVERED',
    UNDELIVERABLE = 'UNDELIVERABLE',
    FAILED = 'FAILED',
}
