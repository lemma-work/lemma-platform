/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScorecardMeasureHistory } from './ScorecardMeasureHistory.js';
import type { ScorecardWindow } from './ScorecardWindow.js';
export type ScorecardPreviewResponse = {
    measures: Array<ScorecardMeasureHistory>;
    /**
     * Measures chosen but not counted by this preview.
     */
    measures_skipped?: number;
    windows: Array<ScorecardWindow>;
};
