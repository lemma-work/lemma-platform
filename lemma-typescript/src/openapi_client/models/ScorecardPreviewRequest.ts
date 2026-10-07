/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScorecardMeasureSpec } from './ScorecardMeasureSpec.js';
export type ScorecardPreviewRequest = {
    /**
     * The day the last week ends, not included. Defaults to today; cannot be after it.
     */
    end?: (string | null);
    /**
     * Saved measures to preview, by key, whether on or off.
     */
    keys?: (Array<string> | null);
    /**
     * One unsaved measure to preview instead. Nothing is saved.
     */
    measure?: (ScorecardMeasureSpec | null);
    /**
     * How many weeks to count, oldest first.
     */
    weeks?: number;
};
