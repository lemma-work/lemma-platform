/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who vouched for a handle.
 *
 * ``CHANNEL``: the platform the message came through, in a payload whose
 * signature was checked (WhatsApp, Telegram), or the receiving mail service's
 * authentication verdict (email). ``MEMBER``: a pod member added it by hand,
 * which says who the member believes it is and nothing about who writes from
 * it -- so it never makes a message count as that contact on its own.
 */
export enum IdentityStrength {
    CHANNEL = 'CHANNEL',
    MEMBER = 'MEMBER',
}
