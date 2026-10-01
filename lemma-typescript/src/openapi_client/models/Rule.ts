/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerValue } from './AnswerValue.js';
/**
 * A deterministic answer, tried before any engine.
 *
 * Either `when`, a JMESPath expression over the rendered state that answers
 * when truthy, or `phrases`, exact matches against the text at `field` after
 * lowercasing, collapsing whitespace and dropping trailing punctuation.
 */
export type Rule = {
    answer: Record<string, AnswerValue>;
    field?: string;
    phrases?: (Array<string> | null);
    when?: (string | null);
};
