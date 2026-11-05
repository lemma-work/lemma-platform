/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionQuestion } from './DecisionQuestion.js';
import type { DecisionRule } from './DecisionRule.js';
/**
 * Configuration for Decision node: `rules`, or a `question`, never both.
 */
export type DecisionNodeConfig = {
    /**
     * Ask one closed question about some evidence and route on the answer. The run waits while it is asked; an answer that cannot be had fails the run rather than taking any branch.
     */
    question?: (DecisionQuestion | null);
    /**
     * Conditions evaluated in order against the run context; the first truthy one picks the next node.
     */
    rules?: Array<DecisionRule>;
};
