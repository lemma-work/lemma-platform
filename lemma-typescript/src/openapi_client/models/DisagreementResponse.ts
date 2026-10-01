/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Answer } from './Answer.js';
import type { AnswerValue } from './AnswerValue.js';
export type DisagreementResponse = {
    answer: (Answer | null);
    expected: AnswerValue;
    question: string;
    row: number;
};
