/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Option } from './Option.js';
/**
 * Any number of the options, including none.
 */
export type MultiChoiceQuestion = {
    options?: (Record<string, Option> | null);
    prompt: string;
    type?: string;
};
