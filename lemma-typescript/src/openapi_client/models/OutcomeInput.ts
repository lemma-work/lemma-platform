/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeKind } from './OutcomeKind.js';
/**
 * What the pod owns, as a person or its agent sets it.
 *
 * The fields are the front matter of the plan file the workspace keeps at
 * ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
 * ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
 * them. The plan's prose (what must be true, how the work is done, who holds
 * what, what never happens) stays in the file.
 */
export type OutcomeInput = {
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
     * What it owns, as one sentence a person can check.
     */
    sentence: string;
    /**
     * The number the measure is held to: 90 with unit '%' for a share, 22 for a finish line of 22 steps. Unset while it is still being worked out.
     */
    target?: (number | null);
    /**
     * '%' when the measure answers a share from 0 to 100; otherwise what it counts, such as 'steps' or 'posts'.
     */
    unit?: (string | null);
};
