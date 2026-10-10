/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue.js';
/**
 * A past case and how it should have been answered.
 */
export type DecisionQuestionExample = {
    /**
     * How that case was answered: one of the question's values, or null when the right answer there was 'can't tell'.
     */
    answer: (boolean | number | string | null);
    /**
     * The evidence of a past case.
     */
    evidence: JsonValue;
};
