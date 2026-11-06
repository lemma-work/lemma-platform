/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeWeekResponse } from './OutcomeWeekResponse.js';
export type OutcomeHistoryResponse = {
    /**
     * Oldest first, ending with this week. Only the weeks read by the pod's current measure: changing the measure starts a new history.
     */
    weeks: Array<OutcomeWeekResponse>;
};
