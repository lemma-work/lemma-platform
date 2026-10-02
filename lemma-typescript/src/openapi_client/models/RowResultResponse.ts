/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Answer } from './Answer.js';
export type RowResultResponse = {
    answers: Record<string, Answer>;
    decision_id: (string | null);
    failed: boolean;
    index: number;
    open: Array<string>;
    row_id: (string | null);
};
