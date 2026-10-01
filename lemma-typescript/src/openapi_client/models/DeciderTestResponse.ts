/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Agreement } from './Agreement.js';
import type { Answer } from './Answer.js';
import type { DisagreementResponse } from './DisagreementResponse.js';
export type DeciderTestResponse = {
    agreement: Record<string, Agreement>;
    answers: Array<Record<string, Answer>>;
    disagreements: Array<DisagreementResponse>;
    open: Array<Array<string>>;
};
