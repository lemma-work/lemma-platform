/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What a grant lets a client do in its pod.
 *
 * The tools split cleanly in two: those that only read and those that write.
 * A finer set would ask a person to reason about tools they have never seen,
 * on a consent screen they will read once.
 *
 * Events are the third, and not a kind of reading. A read happens while the
 * person uses the app; a subscription sends rows to a server the app chose,
 * as they are added, for as long as it keeps refreshing -- including while
 * the person is away. So it is asked for by name and agreed to separately,
 * and a connection made before it existed does not have it.
 */
export enum Scope {
    POD_READ = 'pod:read',
    POD_WRITE = 'pod:write',
    POD_EVENTS = 'pod:events',
}
