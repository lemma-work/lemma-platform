/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Rung } from './Rung.js';
import type { RungOutcome } from './RungOutcome.js';
/**
 * What one rung did for one decision, for the record and for debugging.
 */
export type RungTrace = {
    input_tokens?: (number | null);
    latency_ms?: number;
    model?: (string | null);
    outcome: RungOutcome;
    questions: Array<string>;
    rung: Rung;
};
