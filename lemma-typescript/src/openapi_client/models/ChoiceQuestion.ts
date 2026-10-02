/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Option } from './Option.js';
/**
 * Exactly one of the options.
 *
 * Declared options are the fixed ones; a caller adds its own with each call.
 * That is how one definition answers "which of these?" over the person's
 * pods, the open conversations or the options of a question already put to
 * them, while its `none` or `not_an_answer` is always there to fall back on.
 */
export type ChoiceQuestion = {
    /**
     * The option to answer when nothing clearly applies.
     */
    fallback?: (string | null);
    options?: (Record<string, Option> | null);
    prompt: string;
    type?: string;
};
