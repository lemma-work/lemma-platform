/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodOutcome } from './PodOutcome.js';
export type OutcomeSetResponse = {
    /**
     * When `value` was read; null when it was not.
     */
    measured_at?: (string | null);
    outcome: PodOutcome;
    /**
     * What a new or changed measure answered when it was checked; null when it answered null, or was not read because it did not change.
     */
    value?: (number | null);
};
