/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Job } from './Job.js';
/**
 * What the person does, in answer to the one time they are asked.
 */
export type WorkAnswerRequest = {
    /**
     * What they do, any number of them, each kept once in the order given. Empty with no `other` records that they skipped the question, which still stops it being asked again.
     */
    jobs: Array<Job>;
    /**
     * Something else, in their words. Trimmed; empty is no answer. At most 120 characters once trimmed.
     */
    other?: (string | null);
};
