/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodOutcome } from './PodOutcome.js';
export type OutcomeStandingResponse = {
    measured_at?: (string | null);
    /**
     * What the pod owns, or null when it owns nothing yet.
     */
    outcome: (PodOutcome | null);
    /**
     * Why the measure could not be read, in plain words.
     */
    problem?: (string | null);
    /**
     * What its measure answers now, counted over every row for everyone in the pod alike. Null with no measure, when it answers null, or when it could not be read (see `problem`).
     */
    value?: (number | null);
};
