/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TriageDigest } from './TriageDigest.js';
import type { TriageRoute } from './TriageRoute.js';
/**
 * A decider, and what each of its answers does with the event.
 */
export type TriageConfig = {
    /**
     * At most this many act runs an hour. Past it, act becomes digest when the schedule has one, and ask when it does not.
     */
    act_per_hour?: (number | null);
    /**
     * The pod decider asked about each event.
     */
    decider: string;
    /**
     * Required when any option routes to digest.
     */
    digest?: (TriageDigest | null);
    /**
     * The decider's choice question to route on. Omitted means its only question; saved as the one it resolved to.
     */
    question?: (string | null);
    /**
     * Every declared option of the question, mapped to act, digest, ask or ignore.
     */
    routes: Record<string, TriageRoute>;
};
