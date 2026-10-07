/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScorecardWeekScore } from './ScorecardWeekScore.js';
export type ScorecardMeasureHistory = {
    key: string;
    measure: string;
    /**
     * One per window, oldest first.
     */
    weeks: Array<ScorecardWeekScore>;
};
