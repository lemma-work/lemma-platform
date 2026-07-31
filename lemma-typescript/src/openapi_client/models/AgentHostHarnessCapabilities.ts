/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Harness capabilities the server actually branches on.
 *
 * Only ``images`` changes server behaviour today (it adds the vision
 * capability to the runtime picker). Anything else a host reports is kept
 * verbatim by ``extra: allow`` rather than typed here, so the wire format
 * stays open without inventing fields no code reads.
 */
export type AgentHostHarnessCapabilities = Record<string, any>;
