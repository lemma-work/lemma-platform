/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue.js';
export type DecisionExampleBody = {
    /**
     * How that case was answered, by question key. Questions may be left out; null means the right answer there was 'can't tell'.
     */
    answers: Record<string, (boolean | number | string | Array<string> | null)>;
    /**
     * A past case, as evidence was sent.
     */
    evidence: JsonValue;
};
