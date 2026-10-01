/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionResponse } from './DecisionResponse.js';
export type DecisionListResponse = {
    items: Array<DecisionResponse>;
    /**
     * Pass as `before` for the next, older page.
     */
    next_before?: (string | null);
};
