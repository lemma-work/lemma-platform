/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MeasureStatus } from './MeasureStatus.js';
/**
 * One measure's result for one week, as `score_week` records it.
 */
export type ScorecardWeekScore = {
    counted?: (number | null);
    key: string;
    measure: string;
    met?: (boolean | null);
    reason?: (string | null);
    /**
     * The one string to show: "31 of 40", "none", "1.6 days".
     */
    shown: string;
    status: MeasureStatus;
    target_label: string;
    total?: (number | null);
    value?: (number | null);
};
