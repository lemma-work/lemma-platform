/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DecisionAnswerResponse = {
    /**
     * The provider's probability for this value, when it measures one; for several choices, that of the least certain per-option decision, not of the whole selection. Null from providers that do not, such as a language model.
     */
    confidence: (number | null);
    /**
     * The answer, or null when the provider judged the evidence did not support one (the model provider does; Typesafe always answers the value it judged most likely). Treat null, or a confidence below your own threshold, as unsure. Null is an answer, not a failure: a failure is an error response.
     */
    value: (boolean | number | string | Array<string> | null);
};
