/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Whether somebody made this agent, or the pod came with it.
 *
 * The pod's default assistant used to be the absence of an agent: a
 * conversation naming nobody, synthesised at runtime against one sentinel id
 * shared by every pod. That absence could not be pointed at by a foreign key,
 * so anything wanting to name it grew its own way of saying so — a boolean on
 * the schedule, a second boolean on a channel route, a magic string in a map
 * of who answers whose DMs.
 *
 * A kind is one way of saying it, in the row itself. ``POD_DEFAULT`` is
 * pinned by check constraints to exactly one row per pod, whose id is the
 * pod's own — so "is this the default assistant?" stays a comparison rather
 * than a query, which matters on paths that answer it per request.
 */
export enum AgentKind {
    USER = 'USER',
    POD_DEFAULT = 'POD_DEFAULT',
}
