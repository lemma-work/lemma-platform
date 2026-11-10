/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomePlanAction } from './OutcomePlanAction.js';
/**
 * What the bundle's pod owned, and what the import does with it.
 */
export type ImportPlanOutcome = {
    /**
     * SET: the pod owns nothing yet, and the import sets this. KEEP: the pod already owns something else and keeps it; this is not set. SAME: the pod already owns this (same sentence, kind, target, unit and due).
     */
    action: OutcomePlanAction;
    /**
     * The finish line's date.
     */
    due?: (string | null);
    /**
     * Whether the bundle carries a measure. It is read as the person importing when the import applies; one they cannot read is left off, with a warning, and the rest is still set.
     */
    has_measure?: boolean;
    /**
     * week: a bar held every week. date: a finish line by `due`.
     */
    kind: ImportPlanOutcome.kind;
    /**
     * What it owns, as one sentence.
     */
    sentence: string;
    /**
     * The number its measure is held to, if set.
     */
    target?: (number | null);
    /**
     * '%' for a share; otherwise what it counts.
     */
    unit?: (string | null);
};
export namespace ImportPlanOutcome {
    /**
     * week: a bar held every week. date: a finish line by `due`.
     */
    export enum kind {
        WEEK = 'week',
        DATE = 'date',
    }
}
