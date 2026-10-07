/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DecisionAnswerResponse = {
    /**
     * The provider's probability for this value, when it measures one. Null from providers that do not, such as a language model.
     */
    confidence: (number | null);
    /**
     * The answer, or null when the evidence did not support one. Null is an answer, not a failure: a failure is an error response.
     */
    value: (boolean | number | string | Array<string> | null);
};
