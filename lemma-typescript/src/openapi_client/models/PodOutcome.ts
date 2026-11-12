/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeKind } from './OutcomeKind.js';
/**
 * What the pod owns, as stored on its config.
 */
export type PodOutcome = {
    /**
     * The finish line's date. Required for kind=date, refused for week.
     */
    due?: (string | null);
    /**
     * week: a bar held every week. date: a finish line by `due`.
     */
    kind: OutcomeKind;
    /**
     * One read-only SQL query over the pod's tables that answers a single number: one row, one column. Counted for every member alike, so only someone who can read every row it touches may set it.
     */
    measure?: (string | null);
    /**
     * The tables with row security the measure counts that were checked when it was set, by id: whoever set it administers each. Set by the server. A measure that counts a table with row security not listed here is not read until someone who administers that table sets it again.
     */
    row_security_checked?: Array<string>;
    /**
     * What it owns, as one sentence a person can check.
     */
    sentence: string;
    /**
     * When the pod took this on: set by the server when it is first set, and kept while only its wording, target or measure change.
     */
    since?: (string | null);
    /**
     * The number the measure is held to: 90 with unit '%' for a share, 22 for a finish line of 22 steps. Unset while it is still being worked out.
     */
    target?: (number | null);
    /**
     * '%' when the measure answers a share from 0 to 100; otherwise what it counts, such as 'steps' or 'posts'.
     */
    unit?: (string | null);
};
