/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Whether a profile can take work right now.
 *
 * Derived from the harness and its host at read time, never persisted: the
 * same profile is READY or OFFLINE depending only on whether someone's laptop
 * is awake. Model-provider profiles have no availability - they are reachable
 * whenever their endpoint is.
 */
export enum RuntimeProfileAvailability {
    READY = 'READY',
    OFFLINE = 'OFFLINE',
    NOT_INSTALLED = 'NOT_INSTALLED',
    UNAVAILABLE = 'UNAVAILABLE',
}
