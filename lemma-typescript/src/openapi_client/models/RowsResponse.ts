/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RowResultResponse } from './RowResultResponse.js';
export type RowsResponse = {
    /**
     * Per question, how many rows got each answer.
     */
    counts: Record<string, Record<string, number>>;
    decider_key: string;
    failed: number;
    rows: Array<RowResultResponse>;
};
