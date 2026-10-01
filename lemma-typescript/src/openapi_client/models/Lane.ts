/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who is waiting, which decides how long a rung may take.
 *
 * Interactive: a person is waiting in a conversation. Ambient: an event is
 * being sorted and nobody is watching. Bulk: many rows at once, which yields
 * to the other two.
 */
export enum Lane {
    INTERACTIVE = 'interactive',
    AMBIENT = 'ambient',
    BULK = 'bulk',
}
