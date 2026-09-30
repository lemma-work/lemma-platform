/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What a grant lets a client do in its pod.
 *
 * Two, because the tools split cleanly in two: those that only read and those
 * that write. A finer set would ask a person to reason about tools they have
 * never seen, on a consent screen they will read once.
 */
export enum Scope {
    POD_READ = 'pod:read',
    POD_WRITE = 'pod:write',
}
