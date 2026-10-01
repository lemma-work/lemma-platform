/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerValue } from './AnswerValue.js';
export type AnswerBody = {
    answers: Record<string, AnswerValue>;
    /**
     * Who answered. A person's answer teaches the decider; an agent's is recorded and never becomes an example.
     */
    by?: AnswerBody.by;
};
export namespace AnswerBody {
    /**
     * Who answered. A person's answer teaches the decider; an agent's is recorded and never becomes an example.
     */
    export enum by {
        PERSON = 'person',
        AGENT = 'agent',
    }
}
