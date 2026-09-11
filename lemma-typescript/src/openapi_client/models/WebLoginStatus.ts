/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Whether the stored session is believed to still work.
 *
 * `DEAD` is set when an injection produced a page that still wanted a login.
 * It exists so a person is asked to sign in again *before* a run fails on it,
 * which is the promise `PS-CONN-022` makes for connector credentials.
 */
export enum WebLoginStatus {
    ACTIVE = 'ACTIVE',
    DEAD = 'DEAD',
}
