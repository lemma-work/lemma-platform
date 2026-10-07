/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionAnswerResponse } from './DecisionAnswerResponse.js';
import type { DecisionUsageResponse } from './DecisionUsageResponse.js';
export type DecisionResponse = {
    /**
     * One answer per question, by key.
     */
    answers: Record<string, DecisionAnswerResponse>;
    /**
     * The model that ran.
     */
    model: (string | null);
    /**
     * The provider that answered.
     */
    provider: string;
    usage: DecisionUsageResponse;
};
