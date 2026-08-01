/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Harness capabilities the server actually branches on.
 *
 * ``images`` adds the vision capability to the runtime picker;
 * ``load_session`` is what lets a conversation keep one provider session
 * across turns, so it decides whether a run is dispatched with a
 * ``resume_session_id``. Anything else a host reports is kept verbatim by
 * ``extra: allow`` rather than typed here, so the wire format stays open
 * without inventing fields no code reads.
 */
export type AgentHostHarnessCapabilities = Record<string, any>;
