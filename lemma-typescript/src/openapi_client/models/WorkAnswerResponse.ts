/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Job } from './Job.js';
/**
 * What the person said they do, and when.
 */
export type WorkAnswerResponse = {
    answered_at: string;
    jobs: Array<Job>;
    other?: (string | null);
};
