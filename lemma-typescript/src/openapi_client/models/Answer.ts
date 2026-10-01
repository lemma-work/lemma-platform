/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerValue } from './AnswerValue.js';
import type { Rung } from './Rung.js';
/**
 * One question's answer and where it came from.
 *
 * `distribution` and `confidence` are System One's and exist only when it
 * answered. A model's answer carries neither, and nothing here invents one.
 * `abstained` marks a fallback taken because no rung committed.
 */
export type Answer = {
    abstained?: boolean;
    by: Rung;
    confidence?: (number | null);
    distribution?: (Record<string, number> | null);
    value: AnswerValue;
};
