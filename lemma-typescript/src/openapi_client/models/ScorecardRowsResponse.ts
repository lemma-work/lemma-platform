/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScorecardUnitRow } from './ScorecardUnitRow.js';
export type ScorecardRowsResponse = {
    end: string;
    rows: Array<ScorecardUnitRow>;
    start: string;
    /**
     * More rows fell in the week than are listed.
     */
    truncated?: boolean;
};
