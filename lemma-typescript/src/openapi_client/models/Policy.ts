/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Lane } from './Lane.js';
/**
 * What each rung may answer, and when a rung passes a question on.
 */
export type Policy = {
    /**
     * System One's confidence below which a choice or scale answer is passed on rather than taken.
     */
    abstain_below?: number;
    /**
     * Ask the model when System One abstains. Off, a System One abstention leaves the question open.
     */
    escalate_to_model?: boolean;
    lane?: Lane;
    /**
     * Per question and answer, the System One confidence an engine answer needs to stand. A rung that reports no confidence never meets it.
     */
    require_confidence?: Record<string, Record<string, number>>;
    /**
     * Per question, answers only the rules rung may give. An engine that gives one has not answered.
     */
    rules_only?: Record<string, Array<string>>;
    /**
     * Probabilities of yes inside this band are passed on rather than taken, for yes/no and each option of a multi-choice.
     */
    yes_no_band?: any[];
};
