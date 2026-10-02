/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionNodeQuestion } from './DecisionNodeQuestion.js';
import type { DecisionRule } from './DecisionRule.js';
/**
 * Configuration for Decision node.
 */
export type DecisionNodeConfig = {
    /**
     * Asked only when no rule matched: a decider's closed question, with a branch per option. The node needs rules, a question, or both.
     */
    question?: (DecisionNodeQuestion | null);
    rules?: Array<DecisionRule>;
};
